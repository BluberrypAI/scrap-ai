"""Contract for looking up the next step of a classified waste case."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

GraphKey = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,100}$")]


class Coordinates(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class NextStepRequest(BaseModel):
    waste_code: str = Field(pattern=r"^\d{2}-\d{2}-\d{2}$")
    material: str | None = Field(default=None, max_length=80)
    input_form: str | None = Field(default=None, max_length=80)
    completed_transitions: list[GraphKey] = Field(default_factory=list, max_length=8)
    origin: Coordinates | None = None


class StateLabel(BaseModel):
    key: str
    label: str


class SourceLink(BaseModel):
    publisher: str
    url: str
    locator: str


class NextFacility(BaseModel):
    capability_key: str
    facility_key: str
    label: str
    region: str
    evidence_basis: Literal["published_site_process", "site_role_hypothesis"]
    checks: list[str]
    sources: list[SourceLink]
    location: Coordinates | None = None
    location_source_url: str | None = None
    straight_line_distance_km: float | None = None
    distance_rank: int | None = None


class NextProcess(BaseModel):
    transition_key: str
    label: str
    input_state: StateLabel
    output_state: StateLabel
    operation_steps: list[str]
    checks: list[str]
    facilities: list[NextFacility]
    sources: list[SourceLink]


class NextStepResponse(BaseModel):
    status: Literal["needs_input", "unsupported", "candidates", "end_of_route", "no_route"]
    backend: Literal["neo4j", "reference_demo"]
    waste_code: str
    code_label: str | None = None
    current_state: StateLabel | None = None
    can_finish: bool = False
    options: list[NextProcess] = Field(default_factory=list)
    message: str
    dispatch_approved: Literal[False] = False
