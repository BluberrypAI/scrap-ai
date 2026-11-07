import json

from fastapi.testclient import TestClient

from resource_ai.agents import facility_discovery
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
    ]
    answer = {
        "kind": "form_mentioned",
        "source_id": "source-a",
        "quote": "PP flakes to produce pellets",
    }

    def model(settings, *, instructions, evidence):
        sent = json.loads(evidence)
        assert [item["capability_key"] for item in sent] == ["site-a", "site-b", "site-c"]
        assert sent[0]["recycling_types"] == {"free": ["R-3-3", "R-10"]}
        assert sent[1]["sources"] == sent[2]["sources"] == []
        return json.dumps(
            [
                {**answer, "capability_key": "site-a"},
                {**answer, "capability_key": "site-b"},
            ]
        )

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
                    "content": [{"type": "output_text", "text": json.dumps([lead])}],
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


def test_scoped_evidence_needs_the_right_material_and_incoming_form():
    from resource_ai.agents.intake_review import IntakeBatchRequest, review_batch

    quotes = [
        "This site accepts PP flakes for pellet production.",
        "This site accepts PP pellets for compounding.",
        "This site accepts PET flakes for pellet production.",
        "This site accepts PE film for recycling.",
        "이 공장은 사용 후 알루미늄 음료캔을 압축합니다.",
    ]
    candidates = [
        ("correct-pp", "pp", "shredded", "51-03-01"),
        ("wrong-form", "pp", "shredded", "51-03-01"),
        ("wrong-material", "pp", "shredded", "51-03-01"),
        ("correct-pe", "pe", "film", "51-03-07"),
        ("correct-can", "aluminium", "can", "51-29-03"),
    ]
    packets = []
    rows = []
    for (key, material, form, code), quote in zip(candidates, quotes, strict=True):
        packets.append(
            {
                "capability_key": key,
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
                        "source_id": key,
                        "summary": quote,
                        "publisher": "공장",
                        "url": "https://example.org/plant",
                        "locator": "반입 규격",
                    }
                ],
            }
        )
        rows.append(
            {
                "capability_key": key,
                "kind": "form_mentioned",
                "source_id": key,
                "quote": quote,
            }
        )
    result = review_batch(
        IntakeBatchRequest(candidates=packets), lambda **_: json.dumps(rows, ensure_ascii=False)
    )
    assert {key: item.status for key, item in result.results.items()} == {
        "correct-pp": "reviewed",
        "wrong-form": "needs_evidence",
        "wrong-material": "needs_evidence",
        "correct-pe": "reviewed",
        "correct-can": "reviewed",
    }
    assert not any(item.dispatch_approved for item in result.results.values())
