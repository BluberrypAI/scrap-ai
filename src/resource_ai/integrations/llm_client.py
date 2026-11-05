"""Azure Responses adapter for an explicit, bounded evidence review."""

import re
from urllib.parse import urlsplit

import httpx


class ReviewModelUnavailable(RuntimeError):
    pass


def review_with_azure(settings, *, instructions, evidence):
    try:
        endpoint = urlsplit(settings.azure_openai_endpoint)
    except ValueError:
        raise ReviewModelUnavailable() from None
    host = endpoint.hostname or ""
    path = endpoint.path.rstrip("/")
    project = re.fullmatch(
        r"(/api/projects/[A-Za-z0-9][A-Za-z0-9_.-]{0,127})(?:/openai/v1(?:/responses)?)?", path
    )
    key = settings.azure_openai_api_key.get_secret_value()
    if not (
        endpoint.scheme == "https"
        and endpoint.netloc.lower() == host
        and not endpoint.query
        and not endpoint.fragment
        and key
        and settings.azure_openai_deployment
        and (
            (host.endswith(".services.ai.azure.com") and project)
            or (
                host.endswith(".openai.azure.com")
                and path in {"", "/openai/v1", "/openai/v1/responses"}
            )
        )
    ):
        raise ReviewModelUnavailable()
    prefix = project[1] if project else ""
    body = {
        "model": settings.azure_openai_deployment,
        "store": False,
        "reasoning": {"effort": "high"},
        "max_output_tokens": 2400,
        "instructions": instructions,
        "input": evidence,
    }
    try:
        with httpx.Client(timeout=35, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                f"https://{host}{prefix}/openai/v1/responses", headers={"api-key": key}, json=body
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("status") != "completed":
            raise ReviewModelUnavailable()
        return "".join(
            part["text"]
            for item in payload.get("output", [])
            if item.get("type") == "message"
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        )
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        raise ReviewModelUnavailable() from None
