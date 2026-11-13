"""Azure 주소 검사: 허용한 형식만 {host}[/api/projects/x]/openai/v1/responses로 호출한다."""

from types import SimpleNamespace
from typing import ClassVar

import pytest
from pydantic import SecretStr

from resource_ai.integrations import llm_client
from resource_ai.integrations.llm_client import ReviewModelUnavailable, azure_response


class FakeClient:
    calls: ClassVar[list[str]] = []

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        FakeClient.calls.append(url)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"status": "completed"})


def settings(endpoint):
    return SimpleNamespace(
        azure_openai_endpoint=endpoint,
        azure_openai_api_key=SecretStr("key"),
        azure_openai_deployment="model",
    )


@pytest.fixture(autouse=True)
def fake_http(monkeypatch):
    FakeClient.calls = []
    monkeypatch.setattr(llm_client.httpx, "Client", FakeClient)


@pytest.mark.parametrize(
    ("endpoint", "called"),
    [
        ("https://res.openai.azure.com", "https://res.openai.azure.com/openai/v1/responses"),
        (
            "https://res.openai.azure.com/openai/v1/",
            "https://res.openai.azure.com/openai/v1/responses",
        ),
        (
            "https://res.services.ai.azure.com/api/projects/p1",
            "https://res.services.ai.azure.com/api/projects/p1/openai/v1/responses",
        ),
        # 리소스 단위 Foundry v1 주소 (문진 엔진과 같은 형식)
        (
            "https://res.services.ai.azure.com/openai/v1/responses",
            "https://res.services.ai.azure.com/openai/v1/responses",
        ),
        (
            "https://res.services.ai.azure.com/openai/v1",
            "https://res.services.ai.azure.com/openai/v1/responses",
        ),
    ],
)
def test_allowed_endpoints(endpoint, called):
    assert azure_response(settings(endpoint), {}, timeout=1) == {"status": "completed"}
    assert FakeClient.calls == [called]


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://res.openai.azure.com",
        "https://res.services.ai.azure.com",
        "https://res.services.ai.azure.com/other",
        "https://res.services.ai.azure.com/openai/v1?api-version=1",
        "https://example.com/openai/v1",
        "https://res.openai.azure.com/openai/deployments/x",
    ],
)
def test_rejected_endpoints(endpoint):
    with pytest.raises(ReviewModelUnavailable):
        azure_response(settings(endpoint), {}, timeout=1)
    assert FakeClient.calls == []
