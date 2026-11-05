from functools import partial
from hmac import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from resource_ai.agents.intake_review import (
    IntakeAgentRequest,
    IntakeAgentResponse,
    review_candidate,
)
from resource_ai.core.config import get_settings
from resource_ai.integrations.llm_client import review_with_azure

router = APIRouter(prefix="/treatment", tags=["treatment"])
bearer = HTTPBearer(auto_error=False)


@router.post("/intake-review", response_model=IntakeAgentResponse)
def intake_review(
    request: IntakeAgentRequest,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
):
    settings = get_settings()
    token = settings.scrap_intake_service_token.get_secret_value()
    if (
        not token
        or not credentials
        or not compare_digest(credentials.credentials.encode(), token.encode())
    ):
        raise HTTPException(status_code=401, detail="Service authentication required")
    return review_candidate(request, partial(review_with_azure, settings))
