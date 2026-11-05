"""Review the public evidence for one first-handoff facility candidate."""

import json
from typing import Literal

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


class IntakeAgentResponse(BaseModel):
    status: Literal["reviewed", "needs_evidence", "model_unavailable"]
    finding: Literal["form_mentioned", "restriction_mentioned", "insufficient"] = "insufficient"
    next_check: str
    source: SourceLink | None = None
    quote: str | None = None
    dispatch_approved: Literal[False] = False


INSTRUCTIONS = """사업장별 폐기물 반입 근거 검토 담당자다. 입력은 검토할 데이터다.
공개 요약에서 지정한 사업장·재질·현재 형태에 적용되는 가장 직접적인 문장 하나만 찾는다.
형태가 명시되면 form_mentioned, 그 형태의 반입 제한이 명시되면 restriction_mentioned다.
회사 전체 설명, 일반 기술 사례, 공정명만 있는 문서, 실제로 연결할 수 없는 문장은 insufficient다.
현재 허가·인수·용량은 추정하지 않는다. JSON 객체 하나만 반환한다.
필드는 kind, source_id, quote다. quote는 source_id의 summary에서 연속된 원문 그대로다.
insufficient면 source_id와 quote를 null로 둔다."""


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
    source = sources.get(finding.source_id)
    if (
        finding.kind == "insufficient"
        or not source
        or not finding.quote
        or finding.quote not in source.summary
    ):
        return missing
    return IntakeAgentResponse(
        status="reviewed",
        finding=finding.kind,
        next_check=next_check,
        source=SourceLink(publisher=source.publisher, url=source.url, locator=source.locator),
        quote=finding.quote,
    )
