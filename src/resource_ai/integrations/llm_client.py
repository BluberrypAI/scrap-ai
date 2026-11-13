"""Azure Responses adapter for an explicit, bounded evidence review."""

import json
import re
from urllib.parse import urlsplit

import httpx

# 리소스 단위 Azure OpenAI v1 경로. 호출은 항상 {host}/openai/v1/responses로 보낸다
V1_PATHS = {"/openai/v1", "/openai/v1/responses"}


class ReviewModelUnavailable(RuntimeError):
    pass


JSON_CONTAINER_START = re.compile(
    r'\{(?=\s*(?:"|\}))|\[(?=\s*(?:[\[{"\]]|-?\d|true\b|false\b|null\b))'
)


def parse_model_json(text: str):
    """Read a single JSON payload, allowing a code fence or surrounding prose."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = JSON_CONTAINER_START.search(text)
        if not start:
            raise

    value, end = json.JSONDecoder().raw_decode(text, start.start())
    if JSON_CONTAINER_START.search(text, end):
        raise ValueError("Multiple JSON payloads in model response")
    return value


def azure_response(settings, body, *, timeout):
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
            # Foundry 프로젝트 주소, 또는 리소스 단위 v1 주소(엔진과 같은 형식)
            (host.endswith(".services.ai.azure.com") and (project or path in V1_PATHS))
            or (host.endswith(".openai.azure.com") and (path == "" or path in V1_PATHS))
        )
    ):
        raise ReviewModelUnavailable()
    prefix = project[1] if project else ""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                f"https://{host}{prefix}/openai/v1/responses",
                headers={"api-key": key},
                json={**body, "model": settings.azure_openai_deployment, "store": False},
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("status") != "completed":
            details = payload.get("incomplete_details") or {}
            reason = details.get("reason")
            if reason in {"max_output_tokens", "content_filter"}:
                raise ReviewModelUnavailable(f"incomplete: {reason}")
            raise ReviewModelUnavailable("incomplete response")
        return payload
    except httpx.HTTPStatusError as error:
        raise ReviewModelUnavailable(f"HTTP {error.response.status_code}") from None
    except httpx.TimeoutException:
        raise ReviewModelUnavailable("provider timeout") from None
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        raise ReviewModelUnavailable("provider response error") from None


def review_with_azure(settings, *, instructions, evidence, images=None):
    model_input = evidence
    if images:
        content = [{"type": "input_text", "text": evidence}]
        for image in images:
            content.extend(
                [
                    {"type": "input_text", "text": f"photo_id: {image['photo_id']}"},
                    {"type": "input_image", "image_url": image["data_url"], "detail": "high"},
                ]
            )
        model_input = [{"role": "user", "content": content}]
    payload = azure_response(
        settings,
        {
            "reasoning": {"effort": "high"},
            "max_output_tokens": 6500 if images is not None else 2400,
            "instructions": instructions,
            "input": model_input,
        },
        timeout=80 if images is not None else 35,
    )
    try:
        return "".join(
            part["text"]
            for item in payload.get("output", [])
            if item.get("type") == "message"
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ReviewModelUnavailable() from None
