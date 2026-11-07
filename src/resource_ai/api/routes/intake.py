from functools import partial
from hmac import compare_digest
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from resource_ai.agents.facility_discovery import (
    FacilityDiscoveryRequest,
    FacilityDiscoveryResponse,
    discover,
)
from resource_ai.agents.intake_review import (
    IntakeAgentRequest,
    IntakeAgentResponse,
    IntakeBatchRequest,
    IntakeBatchResponse,
    review_batch,
    review_candidate,
)
from resource_ai.core.config import Settings, get_settings
from resource_ai.integrations.llm_client import review_with_azure

router = APIRouter(prefix="/treatment", tags=["treatment"])
bearer = HTTPBearer(auto_error=False)


def internal_settings(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    settings = get_settings()
    token = settings.scrap_intake_service_token.get_secret_value()
    if (
        not token
        or not credentials
        or not compare_digest(credentials.credentials.encode(), token.encode())
    ):
        raise HTTPException(status_code=401, detail="Service authentication required")
    return settings


InternalSettings = Annotated[Settings, Depends(internal_settings)]


@router.post("/intake-review", response_model=IntakeAgentResponse)
def intake_review(request: IntakeAgentRequest, settings: InternalSettings):
    return review_candidate(request, partial(review_with_azure, settings))


@router.post("/intake-reviews", response_model=IntakeBatchResponse)
def intake_reviews(request: IntakeBatchRequest, settings: InternalSettings):
    return review_batch(request, partial(review_with_azure, settings))


@router.post("/facility-web-search", response_model=FacilityDiscoveryResponse)
def facility_web_search(request: FacilityDiscoveryRequest, settings: InternalSettings):
    return discover(request, settings)
