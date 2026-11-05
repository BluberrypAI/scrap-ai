import pytest
from fastapi.testclient import TestClient

from resource_ai.api.schemas.kg_next_step import NextStepRequest
from resource_ai.knowledge_graph.next_step import next_step, rank_facilities
from resource_ai.main import app


def test_pp_displays_only_immediately_reachable_steps(monkeypatch):
    monkeypatch.setenv("SCRAP_KG_REFERENCE_DEMO", "1")
    client = TestClient(app)
    request = {
        "waste_code": "51-03-01",
        "material": "PP",
        "input_form": "scrap",
        "origin": {"latitude": 36.64, "longitude": 127.45},
    }

    def lookup(completed):
        response = client.post(
            "/kg/next-step", json={**request, "completed_transitions": completed}
        )
        assert response.status_code == 200
        return response.json()

    initial = lookup([])
    assert initial["backend"] == "reference_demo"
    assert not initial["can_finish"]
    assert [item["transition_key"] for item in initial["options"]] == ["pp-prepare"]
    first = initial["options"][0]
    assert first["operation_steps"]
    facility = first["facilities"][0]
    assert facility["facility_key"] == "jungang-gongju"
    assert facility["distance_rank"] == 1
    assert facility["straight_line_distance_km"] > 0
    assert not initial["dispatch_approved"]

    after_sort = lookup(["pp-prepare"])
    assert after_sort["current_state"]["key"] == "pp-shredded"
    assert after_sort["can_finish"]
    assert [item["transition_key"] for item in after_sort["options"]] == ["pp-clean"]
    assert after_sort["options"][0]["facilities"][0]["facility_key"] == "bgf-yesan"
    assert (
        client.post(
            "/kg/next-step", json={**request, "completed_transitions": ["pp-clean"]}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    ("code", "material", "form", "status"),
    [
        ("06-01-01", None, None, "unsupported"),
        ("51-03-01", None, None, "needs_input"),
        ("51-03-01", "aluminium", "can", "unsupported"),
        ("51-29-03", "aluminium", "can", "candidates"),
    ],
)
def test_uses_both_waste_code_and_material_shape(code, material, form, status):
    result = next_step(
        NextStepRequest(waste_code=code, material=material, input_form=form), demo=True
    )
    assert result.status == status


def test_caps_map_ranking_to_five_distinct_sites():
    def candidate(site, distance, capability=None):
        return {
            "facility_key": site,
            "capability_key": capability or site,
            "straight_line_distance_km": distance,
        }

    options = [
        {"facilities": [candidate(str(i), i) for i in range(8, 0, -1)]},
        {"facilities": [candidate("1", 1, "same-site"), candidate("unknown", None)]},
    ]
    rank_facilities(options)
    assert [facility["distance_rank"] for facility in options[0]["facilities"]] == [
        1,
        2,
        3,
        4,
        5,
        None,
        None,
        None,
    ]
    assert [facility["distance_rank"] for facility in options[1]["facilities"]] == [1, None]
