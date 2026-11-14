"""Synthetic evidence tests, never a claim of real facility acceptance."""

import json

import pytest
from pydantic import ValidationError

from resource_ai.agents.intake_review import IntakeBatchRequest, SourceLink, review_batch


@pytest.mark.parametrize(
    "material,form,quote,expected",
    [
        ("pp", "bale", "공장은 PP 압축품을 매입합니다.", "reviewed"),
        ("pe", "shredded", "공장은 HDPE 파쇄품을 사용합니다.", "reviewed"),
        ("abs", "flake", "공장은 ABS 분쇄품을 사용합니다.", "reviewed"),
        ("ps", "flake", "공장은 HIPS 플레이크를 사용합니다.", "reviewed"),
        ("san", "rigid", "공장은 SAN 경질 부품을 수거합니다.", "reviewed"),
        ("pvc", "wallpaper", "공장은 PVC 벽지를 재활용합니다.", "reviewed"),
        ("eps", "ingot", "공장은 EPS 인고트를 사용합니다.", "reviewed"),
        ("pa6", "net", "공장은 PA6 폐어망을 사용합니다.", "reviewed"),
        ("pa66", "flake", "공장은 PA66 분쇄품을 사용합니다.", "reviewed"),
        ("pcabs", "flake", "공장은 PC/ABS 분쇄품을 사용합니다.", "reviewed"),
        ("eps", "ingot", "공장은 EPS 펠릿을 판매합니다.", "needs_evidence"),
        ("pvc", "wallpaper", "공장은 PP 벽지를 사용합니다.", "needs_evidence"),
        ("pa6", "net", "공장은 PA66 폐어망을 사용합니다.", "needs_evidence"),
        ("pa66", "flake", "공장은 PA6 분쇄품을 사용합니다.", "needs_evidence"),
        ("pcabs", "flake", "공장은 PC 분쇄품을 사용합니다.", "needs_evidence"),
        ("ps", "flake", "공장은 EPS 분쇄품을 사용합니다.", "needs_evidence"),
        ("pe", "film", "공장은 PET 필름을 사용합니다.", "needs_evidence"),
    ],
)
def test_new_resin_and_form_guards(material, form, quote, expected):
    packet = {
        "capability_key": "site",
        "site": "합성 공장",
        "site_scope": "published_site_process",
        "material": material,
        "form": form,
        "process": "합성 공정",
        "waste_code": "51-03-01",
        "recycling_types": {},
        "next_check": "수신 기업 확인",
        "sources": [
            {
                "source_id": "public",
                "publisher": "합성 공장",
                "url": "http://example.org/process",
                "locator": "공개 공정",
                "summary": quote,
            }
        ],
    }

    def model(**kwargs):
        assert "제품 판매 목록" in kwargs["instructions"]
        return json.dumps(
            [
                {
                    "capability_key": "site",
                    "kind": "form_mentioned",
                    "source_id": "public",
                    "quote": quote,
                }
            ]
        )

    result = review_batch(IntakeBatchRequest(candidates=[packet]), model).results["site"]
    assert result.status == expected
    assert not result.dispatch_approved
    if expected == "reviewed":
        assert result.source.url == "http://example.org/process"
    packet["site_scope"] = "site_role_hypothesis"
    held = review_batch(IntakeBatchRequest(candidates=[packet]), model).results["site"]
    assert held.status == "needs_evidence" and not held.dispatch_approved


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://example.org"])
def test_citation_protocol_rejects_non_web_urls(url):
    with pytest.raises(ValidationError):
        SourceLink(publisher="example", url=url, locator="page")
