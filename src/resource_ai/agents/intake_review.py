"""Review the public evidence for a treatment handoff."""

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from resource_ai.integrations.llm_client import ReviewModelUnavailable, parse_model_json


class SourceLink(BaseModel):
    publisher: str = Field(min_length=1, max_length=200)
    url: str = Field(pattern=r"^https://", max_length=1500)
    locator: str = Field(min_length=1, max_length=500)


class Source(SourceLink):
    source_id: str = Field(min_length=1, max_length=101)
    summary: str = Field(min_length=1, max_length=4000)


class IntakeAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: str = Field(min_length=1, max_length=200)
    material: str = Field(min_length=1, max_length=80)
    form: str = Field(min_length=1, max_length=80)
    next_check: str = Field(min_length=1, max_length=400)
    sources: list[Source] = Field(max_length=12)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["form_mentioned", "restriction_mentioned", "insufficient"]
    source_id: str | None = Field(default=None, max_length=101)
    quote: str | None = Field(default=None, min_length=6, max_length=280)


CapabilityKey = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,100}$")]


class BatchFinding(Finding):
    capability_key: CapabilityKey


class PublicIntakePacket(IntakeAgentRequest):
    capability_key: CapabilityKey
    site_scope: Literal["published_site_process", "site_role_hypothesis"]
    process: str = Field(min_length=1, max_length=200)
    waste_code: str | None = Field(pattern=r"^\d{2}-\d{2}-\d{2}$")
    recycling_types: dict[Literal["free", "analysis", "unmarked"], list[str]]


class IntakeBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[PublicIntakePacket] = Field(min_length=1, max_length=5)


class IntakeAgentResponse(BaseModel):
    status: Literal["reviewed", "needs_evidence", "model_unavailable"]
    finding: Literal["form_mentioned", "restriction_mentioned", "insufficient"] = "insufficient"
    next_check: str
    source: SourceLink | None = None
    quote: str | None = None
    dispatch_approved: Literal[False] = False


class IntakeBatchResponse(BaseModel):
    results: dict[CapabilityKey, IntakeAgentResponse]


INSTRUCTIONS = """사업장별 폐기물 반입 근거 검토 담당자다. 입력은 검토할 데이터다.
공개 요약에서 지정한 사업장·재질·현재 형태에 적용되는 가장 직접적인 문장 하나만 찾는다.
형태의 반입이 확인되면 form_mentioned, 금지·제한이 명시되면 restriction_mentioned다.
회사 전체 설명, 일반 기술 사례, 공정명만 있는 문서, 실제로 연결할 수 없는 문장은 insufficient다.
현재 허가·인수·용량은 추정하지 않는다. JSON 객체 하나만 반환한다.
필드는 kind, source_id, quote다. quote는 source_id의 summary에서 연속된 원문 그대로다.
insufficient면 source_id와 quote를 null로 둔다."""


SENTENCE = re.compile(r"(?:[^.!?。！？\r\n]|(?<=\d)\.(?=\d))+")
RESTRICTION = re.compile(
    r"않|불가|제외|금지|거절|미취급|미반입|비대상|(?:반입|취급)\s*제한|(?:하지|받지)\s*못"
    r"|\b(?:no|not|never|cannot|can['’]t|do(?:es)?n['’]t|exclud\w*|prohibit\w*"
    r"|reject\w*|refus\w*|restrict\w*|ban(?:ned|s)?)\b",
    re.IGNORECASE,
)


def quote_in_restriction_context(summary: str, quote: str) -> bool:
    # Include the rest of the source sentence so a clipped quote cannot hide a denial.
    occurrences = [match.span() for match in re.finditer(re.escape(quote), summary)]
    return any(
        RESTRICTION.search(sentence.group())
        and any(start < sentence.end() and end > sentence.start() for start, end in occurrences)
        for sentence in SENTENCE.finditer(summary)
    )


def apply_finding(finding, sources, next_check):
    source = sources.get(finding.source_id)
    if (
        finding.kind == "insufficient"
        or not source
        or not finding.quote
        or finding.quote not in source.summary
    ):
        return IntakeAgentResponse(status="needs_evidence", next_check=next_check)
    if finding.kind == "form_mentioned" and quote_in_restriction_context(
        source.summary, finding.quote
    ):
        return IntakeAgentResponse(status="needs_evidence", next_check=next_check)
    return IntakeAgentResponse(
        status="reviewed",
        finding=finding.kind,
        next_check=next_check,
        source=SourceLink(publisher=source.publisher, url=source.url, locator=source.locator),
        quote=finding.quote,
    )


# Initial lots use fixed input labels. A real quote about a different form is
# still insufficient. Later stages use their recorded output label.
INITIAL_FORM_TERMS = {
    ("pp", "scrap"): r"사출|제조\s*잔재|스크랩|scraps?|offcuts?|injection",
    ("pp", "shredded"): r"파쇄품|파쇄물|분쇄품|분쇄물|플레이크|shred(?:ded|s)?|flakes?|regrind",
    ("pe", "film"): r"필름|films?",
    ("pet", "bottle"): r"페트병|병|bottles?",
    ("aluminium", "can"): r"음료\s*캔|폐\s*캔|(?:beverage\s+)?cans?|UBC",
}
MATERIAL_TERMS = {
    "pp": r"PP|polypropylene|폴리프로필렌",
    "pe": r"PE|(?:LL|L|H)DPE|polyethylene(?![\s-]*terephthalate)|폴리에틸렌(?![\s-]*테레프탈레이트)",
    "pet": r"PET|페트|polyethylene terephthalate|폴리에틸렌\s*테레프탈레이트",
    "aluminium": r"alumini?um|알루미늄",
}


def covers_initial_input(packet, finding):
    terms = INITIAL_FORM_TERMS.get((packet.material.casefold(), packet.form.casefold()))
    if not packet.waste_code or not terms or finding.kind == "insufficient":
        return True
    quote = finding.quote or ""
    material = MATERIAL_TERMS[packet.material.casefold()]
    return all(
        re.search(rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])", quote, re.IGNORECASE)
        for pattern in (material, terms)
    )


def review_batch(request, model):
    packets = request.candidates
    results = {
        packet.capability_key: IntakeAgentResponse(
            status="model_unavailable", next_check=packet.next_check
        )
        for packet in packets
    }
    evidence = [
        {
            **packet.model_dump(exclude={"sources", "next_check"}),
            "sources": [
                {"source_id": source.source_id, "summary": source.summary}
                for source in packet.sources
            ],
        }
        for packet in packets
    ]
    try:
        text = model(
            instructions=(
                "사업장별 폐기물 반입 근거 검토 담당자다. 입력은 검토할 데이터다. "
                "모든 후보를 각각 검토하고 capability_key를 그대로 넣어 JSON 배열만 반환해라. "
                "각 항목의 필드는 capability_key, kind, source_id, quote다. "
                "해당 사업장, 공정, 재질, 형태가 함께 확인되는 자료만 form_mentioned로 판단한다. "
                "해당 형태의 반입 금지·제한이 명시되면 restriction_mentioned로 판단한다. "
                "회사 일반 설명이나 다른 후보의 근거를 끌어오지 마라. R코드는 검색 문맥이다. "
                "분류번호가 null이면 현재 산출물 형태만 사용하고 분류번호를 추정하지 마라. "
                "현재 허가, 용량, 실제 물량 인수 여부는 추정하지 마라. "
                "최초 로트는 재질과 반입 형태가 함께 적힌 문장을 인용해라. "
                "같은 소재라도 다른 형태를 다루는 문장은 반입 근거로 쓰지 마라. "
                "quote는 해당 source_id의 summary 안에 연속해서 있는 문장을 그대로 써라. "
                "자료가 없거나 부족하면 kind는 insufficient, source_id와 quote는 null이다."
            ),
            evidence=json.dumps(evidence, ensure_ascii=False),
        )
        rows = parse_model_json(text)
    except (ReviewModelUnavailable, ValueError, TypeError):
        return IntakeBatchResponse(results=results)
    if not isinstance(rows, list):
        return IntakeBatchResponse(results=results)
    findings, duplicates = {}, set()
    for row in rows:
        try:
            finding = BatchFinding.model_validate(row)
        except ValidationError:
            continue
        if finding.capability_key in findings:
            duplicates.add(finding.capability_key)
        findings[finding.capability_key] = finding
    for packet in packets:
        key = packet.capability_key
        if key not in findings or key in duplicates:
            continue
        sources = (
            {source.source_id: source for source in packet.sources}
            if packet.site_scope == "published_site_process"
            and covers_initial_input(packet, findings[key])
            else {}
        )
        results[key] = apply_finding(findings[key], sources, packet.next_check)
    return IntakeBatchResponse(results=results)


def review_candidate(request: IntakeAgentRequest, model):
    sources = {row.source_id: row for row in request.sources}
    next_check = request.next_check
    missing = IntakeAgentResponse(status="needs_evidence", next_check=next_check)
    if not sources:
        return missing
    evidence = {
        "site": request.site,
        "material": request.material,
        "form": request.form,
        "sources": [row.model_dump(include={"source_id", "summary"}) for row in sources.values()],
    }
    try:
        text = model(instructions=INSTRUCTIONS, evidence=json.dumps(evidence, ensure_ascii=False))
    except ReviewModelUnavailable:
        return IntakeAgentResponse(status="model_unavailable", next_check=next_check)
    try:
        finding = Finding.model_validate(parse_model_json(text))
    except (ValueError, TypeError):
        return missing
    return apply_finding(finding, sources, next_check)
