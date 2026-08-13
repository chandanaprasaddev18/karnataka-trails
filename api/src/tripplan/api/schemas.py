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
    # A photograph of a real published place carrying this tag, so the wizard shows
    # what an interest looks like here rather than a stock icon. None is normal:
    # roughly half the corpus has no photograph and the card falls back to a glyph.
    photo: dict[str, Any] | None = None
    # What that photograph shows. Rendered, because a picture on an interest card
    # would otherwise imply the interest is the place.
    photo_caption: str | None = None


class AnchorOut(BaseModel):
    """Something a location-mode trip can be planned around."""

    kind: Literal["poi", "region"]
    slug: str
    label: str
    # "Mudigere taluk" or "Chikkamagaluru district" — two anchors can share a
    # name, and the picker has to be able to tell them apart.
    sublabel: str
    lat: float
    lon: float
    # How much we can plan there: published places and activities within 60 km.
    # Shown in the picker so nobody chooses an anchor with nothing around it.
    nearby: int = 0


class PlanRequestIn(BaseModel):
    """What the wizard posts. Validated here so a bad request is a 422, not a job failure.

    One body for all three modes. Which fields are required depends on `mode`, and
    that rule lives in `engine/brief.py` rather than here, so the CLI is held to
    exactly the same contract as HTTP.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["interest", "location", "district"] = "interest"
    # Required in interest mode; a ranking hint in the other two, hence no
    # min_length. The brief builder rejects an empty list where it matters.
    interests: list[str] = Field(default_factory=list)
    days: int = Field(ge=1, le=14)
    party_size: int = Field(ge=1, le=30)
    budget_band: int = Field(ge=1, le=5)
    origin: str = "Bengaluru"
    district: str = "chikkamagaluru"
    travel_month: int | None = Field(default=None, ge=1, le=12)
    # Location mode: the slug of a POI or region from /api/anchors. A slug rather
    # than raw coordinates, so the anchor is always something we actually hold —
    # free-text coordinates would let a caller plan around the middle of the sea.
    anchor: str | None = None
    radius_km: int | None = Field(default=None, ge=5, le=200)


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
    # Photographs of places inside the district, for the home page mosaic.
    gallery: list[dict[str, Any]] = Field(default_factory=list)
    # Months in which this district has anything open, 1-12. The card shows these
    # instead of a star rating: it is a fact we hold, and it is the single most
    # useful thing to know before planning here — the monsoon closes most of it.
    open_months: list[int] = Field(default_factory=list)


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    published_pois: int
    composer: str
