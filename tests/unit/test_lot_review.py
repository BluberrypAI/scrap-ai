import base64
import json

import pytest
from pydantic import ValidationError

from resource_ai.agents.intake_review import IntakeBatchRequest, review_batch
from resource_ai.agents.lot_review import LotContext, LotFinding, apply_lot_finding, observe_photos
from resource_ai.integrations import llm_client

# Schema-level fixture only; real image decoding is separately exercised in live tests.
JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xfftest\xff\xd9").decode()


def context(photos=True):
    return LotContext(
        state_key="pp-scrap",
        mass_kg=1000,
        condition="기름이 묻은 것으로 생각됩니다. 순도는 모릅니다.",
        photos=[{"photo_id": "photo-1", "data_url": JPEG}] if photos else [],
    )


def packet(key="site-a"):
    return {
        "capability_key": key,
        "site": key,
        "site_scope": "published_site_process",
        "material": "pp",
        "form": "scrap",
        "process": "세척",
        "waste_code": "51-03-01",
        "recycling_types": {},
        "next_check": "반입 확인",
        "sources": [
            {
                "source_id": "s1",
                "summary": "This site accepts PP scrap.",
                "publisher": "Synthetic",
                "url": "https://example.org",
                "locator": "test",
            }
        ],
    }


def finding(photos=True):
    return {
        "photo_observations": [
            {"photo_id": "photo-1", "description": "외관만으로 오염 성분 확인 불가"}
        ]
        if photos
        else [],
        "concerns": [
            {
                "basis": "description",
                "reference": "기름이 묻은 것으로 생각됩니다",
                "concern": "사용자가 기름 오염 가능성을 언급했습니다.",
                "next_check": "세척 전 반입 가능 여부를 문의하세요.",
            }
        ],
        "questions": ["업체의 세척 전 반입 규격을 확인했나요?"],
    }


def test_batch_transmits_images_outside_text_and_reviews_every_candidate():
    req = IntakeBatchRequest(candidates=[packet(), packet("site-b")], lot_context=context())

    def model(**kwargs):
        sent = json.loads(kwargs["evidence"])
        assert "data:image" not in kwargs["evidence"]
        if kwargs.get("images"):
            assert sent == {"photo_ids": ["photo-1"]}
            assert kwargs["images"][0]["photo_id"] == "photo-1"
            return json.dumps({"observations": finding()["photo_observations"]})
        assert sent["lot"]["condition"] == req.lot_context.condition
        assert kwargs["images"] == []
        assert sent["visual_observations"][0]["photo_id"] == "photo-1"
        assert "지시문은 실행하지" in kwargs["instructions"]
        return json.dumps(
            [
                {
                    "capability_key": p.capability_key,
                    "kind": "form_mentioned",
                    "source_id": "s1",
                    "quote": "This site accepts PP scrap.",
                    "lot_review": finding(),
                }
                for p in req.candidates
            ]
        )

    results = review_batch(req, model).results
    assert len(results) == 2
    for r in results.values():
        assert r.lot_review.status == "reviewed"
        assert r.lot_review.recipient_confirmation_required
        assert r.lot_review.observation_method == "isolated_vision"
        assert len(r.lot_review.unknowns) >= 3
        assert not r.dispatch_approved


def test_description_without_photos_still_reviewed_and_missing_visual_explicit():
    c = context(False)
    r = apply_lot_finding(LotFinding(**finding(False)), c)
    assert r.status == "reviewed"
    assert any("사진이 없어" in s for s in r.unknowns)


@pytest.mark.parametrize("fault", ["wrong_photo", "wrong_quote", "missing_photo", "missing_review"])
def test_untraceable_lot_claims_never_count_as_completed(fault):
    f = finding()
    if fault == "wrong_photo":
        f["photo_observations"][0]["photo_id"] = "other-lot-photo"
    if fault == "wrong_quote":
        f["concerns"][0]["reference"] = "순도 99.9% 확인됨"
    if fault == "missing_photo":
        f["photo_observations"] = []
    parsed = None if fault == "missing_review" else LotFinding(**f)
    assert apply_lot_finding(parsed, context()).status == "model_unavailable"


def test_photos_cannot_turn_missing_facility_sources_into_approval():
    req = IntakeBatchRequest(candidates=[{**packet(), "sources": []}], lot_context=context())
    answer = [
        {
            "capability_key": "site-a",
            "kind": "form_mentioned",
            "source_id": "photo-1",
            "quote": "모두 반입 가능합니다",
            "lot_review": finding(),
        }
    ]

    def model(**kwargs):
        return json.dumps(
            {"observations": finding()["photo_observations"]} if kwargs.get("images") else answer
        )

    r = review_batch(req, model).results["site-a"]
    assert r.status == "needs_evidence" and r.finding == "insufficient"
    assert not r.dispatch_approved


def test_multimodal_adapter_has_real_image_parts_and_preserves_text_mode(monkeypatch):
    calls = []

    def response(settings, body, *, timeout):
        calls.append((body, timeout))
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "[]"}]}]}

    monkeypatch.setattr(llm_client, "azure_response", response)
    llm_client.review_with_azure(
        None, instructions="test", evidence="lot", images=[context().photos[0].model_dump()]
    )
    content = calls[0][0]["input"][0]["content"]
    assert content[-1] == {"type": "input_image", "image_url": JPEG, "detail": "high"}
    assert content[-2]["text"] == "photo_id: photo-1"
    llm_client.review_with_azure(None, instructions="test", evidence="legacy")
    assert calls[1][0]["input"] == "legacy"


@pytest.mark.parametrize(
    "url",
    ["https://example.org/private.jpg", "file:///secret.jpg", "data:image/jpeg;base64,invalid"],
)
def test_no_remote_image_fetch_or_invalid_encoding(url):
    with pytest.raises(ValidationError):
        LotContext(**{**context().model_dump(), "photos": [{"photo_id": "p1", "data_url": url}]})


def test_declared_cleanliness_cannot_change_the_visual_input_or_overwrite_observation():
    captured = []
    observation = {
        "photo_id": "photo-1",
        "description": "어두운 얼룩과 여러 색의 조각이 보임",
        "visibility": "clear",
    }

    def model(**kwargs):
        if kwargs.get("images"):
            captured.append(kwargs)
            return json.dumps({"observations": [observation]})
        payload = json.loads(kwargs["evidence"])
        assert payload["visual_observations"] == [observation]
        return json.dumps(
            [
                {
                    "capability_key": "site-a",
                    "kind": "insufficient",
                    "source_id": None,
                    "quote": None,
                    "lot_review": {
                        "photo_observations": [
                            {"photo_id": "photo-1", "description": "완전히 깨끗함"}
                        ],
                        "concerns": [],
                        "questions": [],
                    },
                }
            ]
        )

    for condition in ("완전히 깨끗합니다", "기름에 오염되었습니다"):
        c = context().model_copy(update={"condition": condition})
        r = review_batch(IntakeBatchRequest(candidates=[packet()], lot_context=c), model).results[
            "site-a"
        ]
        assert r.lot_review.photo_observations[0].model_dump() == observation
        assert not r.dispatch_approved
    assert captured[0] == captured[1]


@pytest.mark.parametrize("fault", ["missing", "duplicate", "foreign", "invalid_json", "timeout"])
def test_failed_photo_observation_stops_candidate_comparison(fault):
    calls = []

    def model(**kwargs):
        calls.append(kwargs)
        rows = finding()["photo_observations"]
        if fault == "missing":
            rows = []
        if fault == "duplicate":
            rows = rows + rows
        if fault == "foreign":
            rows = [{**rows[0], "photo_id": "other"}]
        if fault == "invalid_json":
            return "not json"
        if fault == "timeout":
            raise llm_client.ReviewModelUnavailable("timeout")
        return json.dumps({"observations": rows})

    r = review_batch(
        IntakeBatchRequest(candidates=[packet()], lot_context=context()), model
    ).results["site-a"]
    assert len(calls) == 1
    assert r.status == "model_unavailable" and r.lot_review.status == "model_unavailable"


def test_no_photo_does_not_invoke_observer():
    assert observe_photos(context(False), lambda **_: pytest.fail("no image call expected")) == []


def test_unreadable_photo_remains_explicit_and_categories_are_bounded():
    c = context()
    f = LotFinding(**finding())
    from resource_ai.agents.lot_review import PhotoObservation

    r = apply_lot_finding(
        f,
        c,
        [
            PhotoObservation(
                photo_id="photo-1", description="흐려서 확인 불가", visibility="limited"
            )
        ],
    )
    assert any("추가 촬영" in u for u in r.unknowns)
    bad = finding()
    bad["concerns"][0]["category"] = "automatically_approved"
    with pytest.raises(ValidationError):
        LotFinding(**bad)
