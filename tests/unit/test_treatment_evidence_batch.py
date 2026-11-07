import json

import pytest
from fastapi.testclient import TestClient

from resource_ai.agents import facility_discovery
from resource_ai.agents.intake_review import IntakeBatchRequest, review_batch
from resource_ai.api.routes import intake
from resource_ai.core.config import Settings
from resource_ai.main import app


def test_batch_reviews_every_candidate_and_keeps_evidence_with_its_site(monkeypatch):
    settings = Settings(scrap_intake_service_token="local-test-token")
    monkeypatch.setattr(intake, "get_settings", lambda: settings)
    packet = {
        "capability_key": "site-a",
        "site": "화성 테스트 사업장",
        "site_scope": "published_site_process",
        "process": "PP 파쇄품 펠렛화",
        "waste_code": "51-03-01",
        "material": "pp",
        "form": "shredded",
        "recycling_types": {"free": ["R-3-3", "R-10"]},
        "next_check": "현행 허가 확인",
        "sources": [
            {
                "source_id": "source-a",
                "summary": "This site uses PP flakes to produce pellets.",
                "publisher": "Site A",
                "url": "https://example.org/a",
                "locator": "Process",
            }
        ],
    }
    candidates = [
        packet,
        {**packet, "capability_key": "site-b", "site": "다른 사업장", "sources": []},
        {**packet, "capability_key": "site-c", "site": "결과가 누락된 사업장", "sources": []},
        {**packet, "capability_key": "site-d", "site": "근거 부족인 사업장", "sources": []},
    ]
    answer = {
        "kind": "form_mentioned",
        "source_id": "source-a",
        "quote": "PP flakes to produce pellets",
    }

    def model(settings, *, instructions, evidence):
        sent = json.loads(evidence)
        assert [item["capability_key"] for item in sent] == ["site-a", "site-b", "site-c", "site-d"]
        assert sent[0]["recycling_types"] == {"free": ["R-3-3", "R-10"]}
        assert all(not row["sources"] for row in sent[1:])
        payload = json.dumps(
            [
                {**answer, "capability_key": "site-a"},
                {**answer, "capability_key": "site-b"},
                {"kind": "insufficient", "capability_key": "site-d"},
            ]
        )
        return "[검토 결과]\n" + "```json\n" + payload + "\n```\n검토 완료"

    monkeypatch.setattr(intake, "review_with_azure", model)
    with TestClient(app) as client:
        path = "/treatment/intake-reviews"
        assert client.post(path, json={"candidates": candidates}).status_code == 401
        response = client.post(
            path,
            json={"candidates": candidates},
            headers={"Authorization": "Bearer local-test-token"},
        )
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results["site-a"]["status"] == "reviewed"
    assert results["site-a"]["source"]["url"] == "https://example.org/a"
    assert results["site-b"]["status"] == "needs_evidence"
    assert results["site-c"]["status"] == "model_unavailable"
    assert results["site-d"]["status"] == "needs_evidence"
    assert results["site-d"]["source"] is results["site-d"]["quote"] is None
    assert all(result["dispatch_approved"] is False for result in results.values())


def test_live_search_uses_current_form_and_retains_public_sources(monkeypatch):
    lead = {
        "site": "테스트 화성 사업장",
        "address": "경기 화성시",
        "process": "선별 PP 파쇄품 투입",
        "url": "https://example.org/plant",
    }

    def response(settings, body, *, timeout):
        assert "투입 형태 선별 PP 파쇄품" in body["input"]
        assert "재생 펠렛 생산" in body["input"]
        assert "사전 분석 해당 R-10" in body["input"]
        assert "서울·인천·경기" in body["input"]
        assert lead["url"] in body["input"]
        return {
            "output": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {"sources": [{"url": lead["url"]}]},
                },
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "공개 자료를 찾았습니다.\n"
                            + json.dumps([lead, {**lead, "url": "https://example.org/unverified"}])
                            + "\n출처를 확인해 주세요.",
                        }
                    ],
                },
            ]
        }

    monkeypatch.setattr(facility_discovery, "azure_response", response)
    request = facility_discovery.FacilityDiscoveryRequest(
        transition_key="pp-shredded-to-pellet",
        input_form="shredded",
        region="capital",
        waste_code="51-03-01",
        recycling_types={"analysis": ["R-10"]},
        starting_points=[{"site": lead["site"], "url": lead["url"]}],
    )
    result = facility_discovery.discover(request, None)
    assert result.model_dump() == {
        "status": "searched",
        "leads": [lead],
        "dispatch_approved": False,
    }


@pytest.mark.parametrize(
    ("material", "form", "code", "quote", "expected"),
    [
        ("pp", "shredded", "51-03-01", "This site accepts PP flakes.", "reviewed"),
        ("pp", "shredded", "51-03-01", "This site accepts PP pellets.", "needs_evidence"),
        ("pp", "shredded", "51-03-01", "This site accepts PET flakes.", "needs_evidence"),
        ("pe", "film", "51-03-07", "This site accepts polyethylene film.", "reviewed"),
        (
            "pe",
            "film",
            "51-03-07",
            "This site accepts polyethylene terephthalate film.",
            "needs_evidence",
        ),
        (
            "pe",
            "film",
            "51-03-07",
            "폴리에틸렌 테레프탈레이트 필름을 반입합니다.",
            "needs_evidence",
        ),
        (
            "aluminium",
            "can",
            "51-29-03",
            "이 공장은 사용 후 알루미늄 음료캔을 압축합니다.",
            "reviewed",
        ),
    ],
)
def test_scoped_evidence_needs_the_right_material_and_incoming_form(
    material, form, code, quote, expected
):
    request = IntakeBatchRequest(
        candidates=[
            {
                "capability_key": "site",
                "site": "테스트 사업장",
                "site_scope": "published_site_process",
                "material": material,
                "form": form,
                "process": "재활용",
                "waste_code": code,
                "recycling_types": {},
                "next_check": "반입 확인",
                "sources": [
                    {
                        "source_id": "public",
                        "summary": quote,
                        "publisher": "공장",
                        "url": "https://example.org/plant",
                        "locator": "반입 규격",
                    }
                ],
            }
        ]
    )
    rows = [
        {"capability_key": "site", "kind": "form_mentioned", "source_id": "public", "quote": quote}
    ]
    result = review_batch(request, lambda **_: json.dumps(rows, ensure_ascii=False)).results["site"]
    assert result.status == expected
    assert not result.dispatch_approved
