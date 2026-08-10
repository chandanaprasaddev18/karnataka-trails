"""HTTP request and response shapes.

Kept separate from the domain models on purpose. `Itinerary` is the engine's
output contract and is versioned; these are the wire shapes for the API and can
change independently (adding a field to a response should not bump
`SCHEMA_VERSION`).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal["queued", "running", "succeeded", "failed"]


class InterestOut(BaseModel):
    slug: str
    label: str
    description: str | None = None


class PlanRequestIn(BaseModel):
    """What the wizard posts. Validated here so a bad request is a 422, not a job failure."""

    model_config = ConfigDict(extra="forbid")

    interests: list[str] = Field(min_length=1)
    days: int = Field(ge=1, le=14)
    party_size: int = Field(ge=1, le=30)
    budget_band: int = Field(ge=1, le=5)
    origin: str = "Bengaluru"
    district: str = "chikkamagaluru"
    travel_month: int | None = Field(default=None, ge=1, le=12)


class PlanAcceptedOut(BaseModel):
    """202 response. Generation is async, so the client gets a handle, not an itinerary."""

    request_id: UUID
    job_id: UUID
    status: JobStatus
    poll_url: str
    # Returned so the client can store it and send it back as X-Session-Token.
    # Phase 1 has no accounts; this is what makes "my trips" addable later.
    session_token: str


class JobStatusOut(BaseModel):
    status: JobStatus
    # Which pipeline stage is in flight, so the UI can say something better than
    # "loading" during a multi-second compose.
    stage: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    error_code: str | None = None
    error_detail: str | None = None


class PlanStatusOut(BaseModel):
    request_id: UUID
    job: JobStatusOut
    itinerary_id: UUID | None = None
    # The full Itinerary payload once ready. Typed as a dict because it is
    # already a validated, versioned document — re-declaring its shape here would
    # create a second definition to keep in sync.
    itinerary: dict[str, Any] | None = None


class DistrictOut(BaseModel):
    """A district card for the home page."""

    slug: str
    name: str
    published_places: int
    # media[0] is the hero. Typed loosely because it is a display document that
    # must be rendered verbatim, attribution included.
    media: list[dict[str, Any]] = Field(default_factory=list)
    # A few interests this district actually delivers, for the card subtitle.
    top_interests: list[str] = Field(default_factory=list)


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    published_pois: int
    composer: str
