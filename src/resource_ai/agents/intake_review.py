"""Review the public evidence for a treatment handoff."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from resource_ai.integrations.llm_client import ReviewModelUnavailable


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
    source_id: str | None = Field(max_length=101)
    quote: str | None = Field(min_length=6, max_length=280)


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
형태가 명시되면 form_mentioned, 그 형태의 반입 제한이 명시되면 restriction_mentioned다.
회사 전체 설명, 일반 기술 사례, 공정명만 있는 문서, 실제로 연결할 수 없는 문장은 insufficient다.
현재 허가·인수·용량은 추정하지 않는다. JSON 객체 하나만 반환한다.
필드는 kind, source_id, quote다. quote는 source_id의 summary에서 연속된 원문 그대로다.
insufficient면 source_id와 quote를 null로 둔다."""


def apply_finding(finding, sources, next_check):
    source = sources.get(finding.source_id)
    if (
        finding.kind == "insufficient"
        or not source
        or not finding.quote
        or finding.quote not in source.summary
    ):
        return IntakeAgentResponse(status="needs_evidence", next_check=next_check)
    return IntakeAgentResponse(
        status="reviewed",
        finding=finding.kind,
        next_check=next_check,
        source=SourceLink(publisher=source.publisher, url=source.url, locator=source.locator),
        quote=finding.quote,
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
                "해당 형태의 반입 제한이 명시되면 restriction_mentioned로 판단한다. "
                "회사 일반 설명이나 다른 후보의 근거를 끌어오지 마라. R코드는 검색 문맥이다. "
                "분류번호가 null이면 현재 산출물 형태만 사용하고 분류번호를 추정하지 마라. "
                "현재 허가, 용량, 실제 물량 인수 여부는 추정하지 마라. "
                "quote는 해당 source_id의 summary 안에 연속해서 있는 문장을 그대로 써라. "
                "자료가 없거나 부족하면 kind는 insufficient, source_id와 quote는 null이다."
            ),
            evidence=json.dumps(evidence, ensure_ascii=False),
        )
        rows = json.loads(text)
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
        finding = Finding.model_validate_json(text)
    except ValidationError:
        return missing
    return apply_finding(finding, sources, next_check)
