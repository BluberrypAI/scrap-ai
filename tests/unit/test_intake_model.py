from fastapi.testclient import TestClient

from resource_ai.agents.intake_review import IntakeAgentRequest, review_candidate
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
