"""The itinerary schema and the engine's intermediate types.

Three layers live here, and the separation is load-bearing:

* **TripBrief / CandidateSet** — engine inputs. The candidate set is the ONLY
  source of places an itinerary may reference.
* **DraftItinerary** — what a *composer* produces: titles, narrative, rationale
  and candidate REFS. Both the LLM and the deterministic composer emit exactly
  this, which is what lets them be swapped and compared.
* **Itinerary** — the persisted, client-facing object. Every factual field here
  is hydrated server-side from the database or computed by the routing
  provider. A composer cannot write one.

That split is what makes "the model cannot emit a fact" a structural property
rather than a prompt instruction: there is no field on ``DraftItinerary`` for a
coordinate, a distance, a price or a phone number.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tripplan.domain.origins import Origin
from tripplan.domain.taxonomy import (
    ComposerName,
    PlanningMode,
    PoiKind,
    TravelSource,
)

# Bump on any breaking change to the Itinerary payload. Stored alongside every
# persisted itinerary so old rows stay readable by the code that understands them.
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Small shared value objects
# ---------------------------------------------------------------------------


class Money(BaseModel):
    """A price range in integer paise. Never a float — 1 INR = 100 paise exactly."""

    model_config = ConfigDict(frozen=True)

    min_paise: int = Field(ge=0)
    max_paise: int = Field(ge=0)
    per_person: bool = False


class GeoPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    lat: float
    lon: float


class TagRef(BaseModel):
    slug: str
    label: str


class RegionRef(BaseModel):
    slug: str
    name: str


class OriginRef(BaseModel):
    label: str
    lat: float
    lon: float

    @classmethod
    def from_origin(cls, origin: Origin) -> OriginRef:
        return cls(label=origin.label, lat=origin.lat, lon=origin.lon)


class TravelLeg(BaseModel):
    """A single hop. `source` travels WITH the leg, so a part-real, part-estimated
    itinerary is representable during the Phase 3 maps rollout."""

    distance_km: float = Field(ge=0)
    duration_minutes: int = Field(ge=0)
    source: TravelSource


class GuideRef(BaseModel):
    guide_id: UUID
    name: str
    languages: list[str] = Field(default_factory=list)
    contact: dict[str, Any] = Field(default_factory=dict)
    is_verified: bool = False


# ---------------------------------------------------------------------------
# Stage 0 — the normalised request
# ---------------------------------------------------------------------------


class TripBrief(BaseModel):
    """A validated, defaulted trip request. The engine reads only this."""

    model_config = ConfigDict(frozen=True)

    request_id: UUID | None = None
    mode: PlanningMode
    tag_slugs: tuple[str, ...]
    district_slug: str
    days: int = Field(ge=1, le=14)
    party_size: int = Field(ge=1, le=30)
    budget_band: int = Field(ge=1, le=5)
    origin: OriginRef
    # Drives the seasonal filter in stage 1. Explicit rather than "now" so a
    # plan is reproducible and testable.
    travel_month: int = Field(ge=1, le=12)


# ---------------------------------------------------------------------------
# Stage 1 — the candidate set
# ---------------------------------------------------------------------------


class Candidate(BaseModel):
    """One retrieved POI, with the short `ref` the composer will refer to.

    `ref` exists so the prompt never carries a UUID (wasteful) and never carries
    a bare place name (which the model could mutate into something plausible but
    non-existent). A ref either resolves in the candidate set or it does not.
    """

    ref: str
    poi_id: UUID
    kind: PoiKind
    name: str
    summary: str
    region: RegionRef
    point: GeoPoint
    duration_minutes: int | None = None
    cost_band: int | None = None
    cost: Money | None = None
    difficulty: int | None = None
    is_repeatable: bool = False
    tags: tuple[str, ...] = ()
    # How well this matched the requested interests (max poi_tags.weight).
    match_weight: int = 0
    data_confidence: int = 3
    # True only once a human has checked the row. Drives the `unverified_data`
    # warning, so the client can never present hand-compiled data as confirmed.
    is_verified: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)
    media: list[dict[str, Any]] = Field(default_factory=list)
    guides: list[GuideRef] = Field(default_factory=list)


class CandidateSet(BaseModel):
    """Everything the composer is allowed to choose from."""

    places: list[Candidate] = Field(default_factory=list)
    stays: list[Candidate] = Field(default_factory=list)
    activities: list[Candidate] = Field(default_factory=list)

    def all(self) -> list[Candidate]:
        return [*self.places, *self.stays, *self.activities]

    def by_ref(self) -> dict[str, Candidate]:
        return {c.ref: c for c in self.all()}

    def refs(self) -> set[str]:
        return {c.ref for c in self.all()}

    def is_empty(self) -> bool:
        return not self.places and not self.activities

    def fingerprint(self) -> str:
        """Stable hash of the candidate ID set, for reproducibility.

        Hashing POI ids (not refs) means the same underlying selection produces
        the same fingerprint even if capping order shifts a ref label.
        """
        ids = sorted(str(c.poi_id) for c in self.all())
        return hashlib.sha256("|".join(ids).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Stage 2/3 — what a composer may produce
# ---------------------------------------------------------------------------


class DraftItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    why_chosen: str | None = None


class DraftDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_number: int = Field(ge=1)
    title: str
    narrative: str | None = None
    stay_ref: str | None = None
    items: list[DraftItem] = Field(default_factory=list)


class DraftItinerary(BaseModel):
    """A composer's output: selection, ordering intent, and prose. No facts.

    Note what is absent: coordinates, distances, durations, prices, contacts,
    times. Those are hydrated in stage 5 from the database, so a composer has no
    field in which to invent one.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    narrative: str | None = None
    days: list[DraftDay] = Field(default_factory=list)

    def referenced_refs(self) -> set[str]:
        refs: set[str] = set()
        for day in self.days:
            if day.stay_ref:
                refs.add(day.stay_ref)
            refs.update(item.ref for item in day.items)
        return refs


# ---------------------------------------------------------------------------
# Stage 5 — the persisted, client-facing itinerary
# ---------------------------------------------------------------------------


WarningCode = Literal[
    "long_travel_day",
    "no_stay_available",
    "thin_day",
    "unverified_data",
    "fallback_composer",
    "permit_required",
    # A requested interest had no candidates at all — usually the seasonal
    # filter. Without this the plan silently delivers a different trip from the
    # one that was asked for.
    "interest_unmet",
    # A day consumed by the approach drive. Honest to state rather than to pad
    # with a stop nobody can reach before dark.
    "arrival_day",
    "late_finish",
    # More activity than the day's budget allows — the opposite of thin_day, and
    # conflating the two would tell the traveller the wrong thing.
    "packed_day",
]


class ItineraryWarning(BaseModel):
    """Surfaced to the client. An itinerary that is stretching should say so."""

    code: WarningCode
    day_number: int | None = None
    message: str


class ItineraryItem(BaseModel):
    slot: int = Field(ge=1)
    kind: PoiKind
    poi_id: UUID
    name: str
    summary: str
    why_chosen: str | None = None
    start_time_estimate: str | None = None
    duration_minutes: int | None = None
    cost: Money | None = None
    point: GeoPoint
    media: list[dict[str, Any]] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)
    leg_from_previous: TravelLeg | None = None
    guides: list[GuideRef] = Field(default_factory=list)


class StayCard(BaseModel):
    poi_id: UUID
    name: str
    stay_type: str
    per_night: Money | None = None
    point: GeoPoint
    contact: dict[str, Any] = Field(default_factory=dict)
    media: list[dict[str, Any]] = Field(default_factory=list)
    meals_included: bool = False
    amenities: list[str] = Field(default_factory=list)


class ItineraryDay(BaseModel):
    day_number: int = Field(ge=1)
    title: str
    narrative: str | None = None
    travel: TravelLeg | None = None
    stay: StayCard | None = None
    items: list[ItineraryItem] = Field(default_factory=list)


class ReturnLeg(BaseModel):
    from_poi_id: UUID | None = None
    to: OriginRef
    distance_km: float = Field(ge=0)
    duration_minutes: int = Field(ge=0)
    source: TravelSource


class BriefSummary(BaseModel):
    """The brief as rendered back to the client."""

    mode: PlanningMode
    interests: list[TagRef] = Field(default_factory=list)
    days: int
    party_size: int
    budget_band: int
    origin: OriginRef
    district: RegionRef


class ItinerarySummary(BaseModel):
    title: str
    narrative: str | None = None
    total_distance_km: float = 0.0
    total_travel_minutes: int = 0
    estimated_cost: Money | None = None
    warnings: list[ItineraryWarning] = Field(default_factory=list)


class Itinerary(BaseModel):
    """The versioned output contract. This is what lands in itineraries.payload."""

    schema_version: int = SCHEMA_VERSION
    itinerary_id: UUID | None = None
    request_id: UUID | None = None
    generated_at: datetime
    composer: ComposerName
    llm_provider: str | None = None
    llm_model: str | None = None
    candidate_set_hash: str | None = None
    brief: BriefSummary
    summary: ItinerarySummary
    days: list[ItineraryDay] = Field(default_factory=list)
    return_leg: ReturnLeg | None = None

    def referenced_poi_ids(self) -> list[tuple[UUID, int, int]]:
        """``(poi_id, day_number, slot)`` for the itinerary_pois audit trail.

        Slot 0 is the day's stay, matching the convention in migration 001.
        """
        out: list[tuple[UUID, int, int]] = []
        for day in self.days:
            if day.stay is not None:
                out.append((day.stay.poi_id, day.day_number, 0))
            for item in day.items:
                out.append((item.poi_id, day.day_number, item.slot))
        return out
