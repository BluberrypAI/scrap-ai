import json

import pytest
from fastapi.testclient import TestClient

from resource_ai.agents.intake_review import IntakeAgentRequest, review_candidate
from resource_ai.integrations.llm_client import parse_model_json
from resource_ai.main import app


def test_intake_review_only_cites_exact_approved_summary():
    request = IntakeAgentRequest(
        site="Synthetic site",
        material="pp",
        form="scrap",
        next_check="Check intake",
        sources=[
            {
                "source_id": "public",
                "summary": "The site publishes a PP scrap example.",
                "publisher": "Synthetic publisher",
                "url": "https://example.com",
                "locator": "Example",
            }
        ],
    )
    grounded = review_candidate(
        request,
        lambda **_: '{"kind":"form_mentioned","source_id":"public","quote":"PP scrap example"}',
    )
    assert grounded.status == "reviewed"
    assert grounded.source.url == "https://example.com"
    assert not grounded.dispatch_approved

    fabricated = review_candidate(
        request,
        lambda **_: '{"kind":"form_mentioned","source_id":"public","quote":"accepts all PP"}',
    )
    assert fabricated.status == "needs_evidence"


def test_intake_review_requires_internal_service_authentication():
    response = TestClient(app).post(
        "/treatment/intake-review",
        json={
            "site": "Site",
            "material": "pp",
            "form": "scrap",
            "next_check": "Check intake",
            "sources": [],
        },
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("summary", "kind", "status"),
    [
        ("PP 스크랩은 반입하지 않습니다.", "form_mentioned", "needs_evidence"),
        ("PP 스크랩 0.5t 이하는 반입 불가입니다.", "form_mentioned", "needs_evidence"),
        ("This site does not accept PP 스크랩.", "form_mentioned", "needs_evidence"),
        ("PP 스크랩을 받습니다. PE 필름은 제외합니다.", "form_mentioned", "reviewed"),
        (
            "PP 스크랩을 받습니다. PP 스크랩 중 오염품은 제외합니다.",
            "form_mentioned",
            "needs_evidence",
        ),
        ("PP 스크랩은 반입하지 않습니다.", "restriction_mentioned", "reviewed"),
    ],
)
def test_intake_checks_the_source_sentence_around_a_clipped_quote(summary, kind, status):
    request = IntakeAgentRequest(
        site="테스트 사업장",
        material="pp",
        form="scrap",
        next_check="반입 확인",
        sources=[
            {
                "source_id": "public",
                "summary": summary,
                "publisher": "공장",
                "url": "https://example.org/plant",
                "locator": "반입 규격",
            }
        ],
    )
    result = review_candidate(
        request,
        lambda **_: (
            "검토 결과입니다.\n"
            + json.dumps(
                {"kind": kind, "source_id": "public", "quote": "PP 스크랩"}, ensure_ascii=False
            )
        ),
    )
    assert result.status == status
    if status == "reviewed":
        assert result.finding == kind
    assert not result.dispatch_approved


@pytest.mark.parametrize(
    "response", ['검토 결과: [{"kind":"insufficient"}', '결과: [{"kind":"insufficient"}] 다음: []']
)
def test_model_json_rejects_truncated_and_ambiguous_responses(response):
    with pytest.raises(ValueError):
        parse_model_json(response)
