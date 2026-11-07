import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from resource_ai.api.schemas.facility_assessment import (
    AssessedPlansRequest,
    AssessedPlansResponse,
    FacilityAssessmentRequest,
    FacilityAssessmentResponse,
)
from resource_ai.api.schemas.facility_search import FacilitySearchRequest, FacilitySearchResponse
from resource_ai.api.schemas.graph import CandidateRequest, CandidateResponse
from resource_ai.api.schemas.kg_next_step import NextStepRequest, NextStepResponse
from resource_ai.api.schemas.kg_planning import PathPlanningRequest, PathPlanningResponse
from resource_ai.api.schemas.kg_pool import CandidatePoolRequest, CandidatePoolResponse
from resource_ai.core.dependencies import get_graph_query
from resource_ai.knowledge_graph.graph_store import GraphStoreError
from resource_ai.knowledge_graph.next_step import next_step
from resource_ai.knowledge_graph.ontology import ontology
from resource_ai.knowledge_graph.query import GraphQuery

router = APIRouter(prefix="/kg", tags=["knowledge-graph"])


@router.post("/next-step", response_model=NextStepResponse)
def next_process_step(request: NextStepRequest):
    try:
        return next_step(request, demo=os.getenv("SCRAP_KG_REFERENCE_DEMO") == "1")
    except GraphStoreError as error:
        raise HTTPException(
            status_code=409 if error.code == "invalid-progress" else 503,
            detail={"code": error.code, "message": str(error)},
        ) from None


@router.get("/planning-coverage")
def planning_coverage(query: Annotated[GraphQuery, Depends(get_graph_query)]):
    try:
        return query.planning_coverage()
    except GraphStoreError as error:
        raise HTTPException(
            status_code=503, detail={"code": error.code, "message": str(error)}
        ) from None


@router.post("/facility-assessment", response_model=FacilityAssessmentResponse)
def facility_assessment(
    request: FacilityAssessmentRequest, query: Annotated[GraphQuery, Depends(get_graph_query)]
):
    try:
        return query.facility_assessment(request)
    except GraphStoreError as error:
        raise HTTPException(
            status_code=400 if error.code == "invalid-capability" else 503,
            detail={"code": error.code, "message": str(error)},
        ) from None


@router.post("/assessed-plans", response_model=AssessedPlansResponse)
def assessed_plans(
    request: AssessedPlansRequest, query: Annotated[GraphQuery, Depends(get_graph_query)]
):
    try:
        return query.assessed_plans(request)
    except GraphStoreError as error:
        raise HTTPException(
            status_code={"invalid-goal": 400, "invalid-cursor": 400, "stale-cursor": 409}.get(
                error.code, 503
            ),
            detail={"code": error.code, "message": str(error)},
        ) from None


@router.post("/facility-candidates", response_model=FacilitySearchResponse)
def facility_candidates(
    request: FacilitySearchRequest, query: Annotated[GraphQuery, Depends(get_graph_query)]
):
    try:
        return query.facility_candidates(request)
    except GraphStoreError as error:
        status = {"invalid-transition": 400, "invalid-cursor": 400, "stale-cursor": 409}.get(
            error.code, 503
        )
        raise HTTPException(
            status_code=status, detail={"code": error.code, "message": str(error)}
        ) from None


@router.post("/process-plans", response_model=PathPlanningResponse)
def process_plans(
    request: PathPlanningRequest, query: Annotated[GraphQuery, Depends(get_graph_query)]
):
    try:
        return query.process_plans(request)
    except GraphStoreError as error:
        status = {"invalid-goal": 400, "invalid-cursor": 400, "stale-cursor": 409}.get(
            error.code, 503
        )
        raise HTTPException(
            status_code=status, detail={"code": error.code, "message": str(error)}
        ) from None


@router.post("/candidate-pool", response_model=CandidatePoolResponse)
def candidate_pool(
    request: CandidatePoolRequest, query: Annotated[GraphQuery, Depends(get_graph_query)]
):
    try:
        return query.candidate_pool(request)
    except GraphStoreError as error:
        status = {"invalid-cursor": 400, "stale-cursor": 409}.get(error.code, 503)
        raise HTTPException(
            status_code=status, detail={"code": error.code, "message": str(error)}
        ) from None


@router.get("/ontology")
def get_ontology() -> dict:
    return ontology()


@router.post("/candidates", response_model=CandidateResponse)
def candidates(request: CandidateRequest, query: Annotated[GraphQuery, Depends(get_graph_query)]):
    try:
        return query.search(request)
    except GraphStoreError as error:
        raise HTTPException(
            status_code=503, detail={"code": error.code, "message": str(error)}
        ) from None
