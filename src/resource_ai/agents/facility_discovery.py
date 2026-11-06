"""Find source-backed public facility leads for one PP process."""

import json
import logging
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from resource_ai.integrations.llm_client import ReviewModelUnavailable, azure_response

logger = logging.getLogger(__name__)

Region = Literal["capital", "chungcheong", "honam", "southeast", "daegyeong", "gangwon", "jeju"]
REGIONS = {
    "capital": "서울·인천·경기",
    "chungcheong": "대전·세종·충남·충북",
    "honam": "광주·전남·전북",
    "southeast": "부산·울산·경남",
    "daegyeong": "대구·경북",
    "gangwon": "강원",
    "jeju": "제주",
}
PROCESS = {
    "pp-prepare": "반입 선별·파쇄",
    "pp-clean": "파쇄품 세척",
    "pp-pelletise": "세척품 압출·재생 펠렛 생산",
    "pp-shredded-to-pellet": "선별된 파쇄품을 원료로 재생 펠렛 생산",
    "pp-compound": "재생 펠렛 컴파운딩",
}
FORMS = {
    "scrap": "사출 잔재·스크랩",
    "rigid": "경질 조각",
    "film": "필름",
    "shredded": "선별 PP 파쇄품·분쇄품(플레이크)",
    "flake": "플레이크",
    "washed": "세척품",
    "pellet": "재생 펠렛",
}
GROUPS = {"free": "사전 분석 비해당", "analysis": "사전 분석 해당", "unmarked": "요건 미표시"}


class Lead(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    site: str = Field(min_length=2, max_length=150)
    address: str | None = Field(max_length=240)
    process: str = Field(min_length=2, max_length=240)
    url: str = Field(min_length=8, max_length=1500)


def source_key(url):
    if not isinstance(url, str):
        return None
    try:
        p = urlsplit(url)
        if (
            p.scheme != "https"
            or not p.hostname
            or p.username
            or p.password
            or p.port not in (None, 443)
        ):
            return None
        return p.hostname, p.path.rstrip("/"), p.query
    except ValueError:
        return None


class PublicReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: str = Field(min_length=2, max_length=150)
    url: str = Field(pattern=r"^https://[A-Za-z0-9.-]+(?::443)?(?:[/?#]|$)", max_length=1500)


class FacilityDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transition_key: Literal[
        "pp-prepare", "pp-clean", "pp-pelletise", "pp-shredded-to-pellet", "pp-compound"
    ]
    input_form: Literal["scrap", "rigid", "film", "shredded", "flake", "washed", "pellet"]
    region: Region
    waste_code: Literal["51-03-01", "51-03-07"] | None
    recycling_types: dict[Literal["free", "analysis", "unmarked"], list[str]] | None
    starting_points: list[PublicReference] = Field(default_factory=list, max_length=3)


class FacilityDiscoveryResponse(BaseModel):
    status: Literal["searched", "unavailable"]
    leads: list[Lead] = Field(default_factory=list)
    dispatch_approved: Literal[False] = False


def extract(response):
    calls = [
        item
        for item in response.get("output", [])
        if item.get("type") == "web_search_call" and item.get("status") == "completed"
    ]
    if not calls:
        raise ValueError("완료된 Azure 웹 검색 호출이 없습니다.")
    sources = {
        source_key(source.get("url"))
        for call in calls
        for source in call.get("action", {}).get("sources", [])
    } - {None}
    content = [
        part
        for item in response.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    ]
    sources.update(
        key
        for part in content
        for note in part.get("annotations", [])
        if note.get("type") == "url_citation" and (key := source_key(note.get("url")))
    )
    raw = "\n".join(part["text"] for part in content).strip()
    raw = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", raw).strip()
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise TypeError("검색 결과는 JSON 배열이어야 합니다.")
    leads = {}
    for item in parsed[:15]:
        try:
            lead = Lead.model_validate(item)
        except ValidationError:
            continue
        key = source_key(lead.url)
        if key and key in sources:
            identity = (lead.site.casefold(), (lead.address or "").casefold(), key)
            leads.setdefault(identity, lead.model_dump())
    if parsed and not leads:
        logger.warning(
            "Facility discovery had no source-matched lead (%d cited URLs)", len(sources)
        )
    return list(leads.values()), len(calls), len(sources)


def discover(request, settings):
    hints = [
        f"{GROUPS[group]} {code}"
        for group, codes in (request.recycling_types or {}).items()
        for code in codes[:10]
        if re.fullmatch(r"R-(?:[1-9]|1[01])(?:-[1-9][0-9]?)?", code)
    ]
    starting_points = "\n".join(f"{point.site} · {point.url}" for point in request.starting_points)
    body = {
        "reasoning": {"effort": "high"},
        "max_output_tokens": 3500,
        "max_tool_calls": 2,
        "tool_choice": "required",
        "tools": [
            {"type": "web_search", "user_location": {"type": "approximate", "country": "KR"}}
        ],
        "include": ["web_search_call.action.sources"],
        "instructions": (
            "공개 웹에서 지정 권역의 해당 투입 형태와 공정이 확인되는 사업장을 최대 5곳 찾아라. "
            "사업장 공식 자료와 공공 자료를 우선하고 본사 주소를 공장 주소로 쓰지 마라. "
            "투입 형태와 해당 사업장 공정이 함께 나타난 실제 처리 사례도 포함하라. "
            "투입 형태 또는 해당 사업장 공정이 확인되지 않으면 제외하라. "
            "공개 검색 단서가 있으면 원문을 검색해 확인하되, 지정 권역을 벗어나면 제외하라. "
            "R코드는 검색 문맥으로만 쓰고 회사의 실제 허가, 이번 물량 인수, 가격, 용량은 추정하지 마라. "
            "url 값에 검색 도구에서 확인된 정확한 원문 URL을 써라. "
            "JSON 배열만 반환하라. 키는 site, address, process, url이다."
        ),
        "input": (
            f"권역 {REGIONS[request.region]}\n재질 PP\n투입 형태 {FORMS[request.input_form]}"
            f"\n필요 공정 {PROCESS[request.transition_key]}"
            f"\n분류번호 {request.waste_code or '후속 산출물의 분류 미확인'}"
            f"\n관련 재활용 유형 {', '.join(hints) or '확인 필요'}"
            f"\n공개 검색 단서\n{starting_points or '없음'}"
        ),
    }
    try:
        leads, _, _ = extract(azure_response(settings, body, timeout=60))
        return FacilityDiscoveryResponse(status="searched", leads=leads[:5])
    except ReviewModelUnavailable as error:
        logger.warning("Facility discovery Azure request failed: %s", error)
        return FacilityDiscoveryResponse(status="unavailable")
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        logger.warning("Facility discovery response rejected: %s", type(error).__name__)
        return FacilityDiscoveryResponse(status="unavailable")
