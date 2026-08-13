"""Stage 4 — routing enrichment.

The composer decides *which* places go together on *which* day. This stage
decides the order they are visited in and how long the driving takes, using a
`RoutingProvider`. That division is a stated product constraint: grouping is
judgement, geography is arithmetic, and the model does not do arithmetic here.

Concretely, this stage:

* re-orders each day's stops by nearest-neighbour from wherever the day starts,
  discarding the composer's ordering intent while keeping its grouping;
* computes a leg for every hop, including origin -> first stop on day 1 and
  last stop -> that night's stay;
* memoises POI-to-POI legs in `travel_estimates` so a second plan over the same
  district is cheaper, and so Phase 3 can compare real ETAs against these;
* computes the return leg home.

Legs involving the origin are NOT cached: the origin is user input, not a POI,
and `travel_estimates` is keyed by POI foreign keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from tripplan.db import DbConn
from tripplan.domain.models import (
    Candidate,
    CandidateSet,
    DraftItinerary,
    GeoPoint,
    TravelLeg,
    TripBrief,
)
from tripplan.domain.taxonomy import TravelSource
from tripplan.observability.logging import get_logger
from tripplan.routing.base import RoutingProvider, haversine_km
from tripplan.store import travel as travel_store

log = get_logger(__name__)


@dataclass
class RoutedItem:
    candidate: Candidate
    why_chosen: str | None = None
    leg_from_previous: TravelLeg | None = None


@dataclass
class RoutedDay:
    day_number: int
    title: str
    narrative: str | None
    stay: Candidate | None
    items: list[RoutedItem] = field(default_factory=list)
    # Total driving for the day: approach + between stops + on to the stay.
    travel: TravelLeg | None = None


@dataclass
class RoutedPlan:
    days: list[RoutedDay]
    return_leg: TravelLeg | None
    return_from: Candidate | None
    total_distance_km: float
    total_travel_minutes: int


class LegResolver:
    """Provides legs, preferring a cached row over a fresh computation."""

    def __init__(
        self,
        provider: RoutingProvider,
        cached: dict[tuple[UUID, UUID], TravelLeg] | None = None,
    ) -> None:
        self._provider = provider
        self._cached = cached or {}
        self.computed: list[tuple[UUID, UUID, TravelLeg]] = []

    def between(
        self,
        a: GeoPoint,
        b: GeoPoint,
        *,
        from_poi: UUID | None = None,
        to_poi: UUID | None = None,
    ) -> TravelLeg:
        if from_poi is not None and to_poi is not None:
            hit = self._cached.get((from_poi, to_poi))
            if hit is not None:
                return hit
        leg = self._provider.leg(a, b)
        if from_poi is not None and to_poi is not None and from_poi != to_poi:
            self.computed.append((from_poi, to_poi, leg))
            self._cached[(from_poi, to_poi)] = leg
        return leg


def _order_by_proximity(items: list[Candidate], start: GeoPoint) -> list[Candidate]:
    """Nearest-neighbour from the day's starting point.

    Greedy rather than optimal on purpose: at 2-5 stops a day the optimal tour
    and the greedy tour are almost always the same, and a traveller cannot tell
    the difference between two orderings that differ by four minutes.
    """
    remaining = list(items)
    ordered: list[Candidate] = []
    cursor = start
    while remaining:
        nearest = min(remaining, key=lambda c: haversine_km(cursor, c.point))
        ordered.append(nearest)
        remaining.remove(nearest)
        cursor = nearest.point
    return ordered


def route(
    draft: DraftItinerary,
    candidates: CandidateSet,
    brief: TripBrief,
    resolver: LegResolver,
) -> RoutedPlan:
    """Turn a validated draft into an ordered, measured plan."""
    by_ref = candidates.by_ref()
    origin = GeoPoint(lat=brief.origin.lat, lon=brief.origin.lon)

    routed_days: list[RoutedDay] = []
    total_km = 0.0
    total_minutes = 0

    # Where the traveller wakes up. Day 1 starts at the origin; later days start
    # at the previous night's stay (falling back to the last stop if there was
    # no stay, which is the honest interpretation of "no bed booked").
    cursor_point = origin
    cursor_poi: UUID | None = None
    last_candidate: Candidate | None = None

    for day in sorted(draft.days, key=lambda d: d.day_number):
        day_items = [by_ref[i.ref] for i in day.items if i.ref in by_ref]
        why = {i.ref: i.why_chosen for i in day.items}
        stay = by_ref.get(day.stay_ref) if day.stay_ref else None

        ordered = _order_by_proximity(day_items, cursor_point)

        day_km = 0.0
        day_minutes = 0
        routed_items: list[RoutedItem] = []

        for candidate in ordered:
            leg = resolver.between(
                cursor_point,
                candidate.point,
                from_poi=cursor_poi,
                to_poi=candidate.poi_id,
            )
            routed_items.append(
                RoutedItem(
                    candidate=candidate,
                    why_chosen=why.get(candidate.ref),
                    leg_from_previous=leg,
                )
            )
            day_km += leg.distance_km
            day_minutes += leg.duration_minutes
            cursor_point = candidate.point
            cursor_poi = candidate.poi_id
            last_candidate = candidate

        # The drive to that night's bed is part of the day's travel, not a
        # free transition — it is often the longest leg of an evening.
        if stay is not None:
            to_stay = resolver.between(
                cursor_point, stay.point, from_poi=cursor_poi, to_poi=stay.poi_id
            )
            day_km += to_stay.distance_km
            day_minutes += to_stay.duration_minutes
            cursor_point = stay.point
            cursor_poi = stay.poi_id
            last_candidate = stay

        routed_days.append(
            RoutedDay(
                day_number=day.day_number,
                title=day.title,
                narrative=day.narrative,
                stay=stay,
                items=routed_items,
                travel=TravelLeg(
                    distance_km=round(day_km, 2),
                    duration_minutes=day_minutes,
                    source=_source_of(routed_items),
                ),
            )
        )
        total_km += day_km
        total_minutes += day_minutes

    return_leg: TravelLeg | None = None
    if last_candidate is not None:
        return_leg = resolver.between(cursor_point, origin)
        total_km += return_leg.distance_km
        total_minutes += return_leg.duration_minutes

    log.info(
        "engine.routing",
        days=len(routed_days),
        total_km=round(total_km, 1),
        total_minutes=total_minutes,
        legs_computed=len(resolver.computed),
    )

    return RoutedPlan(
        days=routed_days,
        return_leg=return_leg,
        return_from=last_candidate,
        total_distance_km=round(total_km, 2),
        total_travel_minutes=total_minutes,
    )


def _source_of(items: list[RoutedItem]) -> TravelSource:
    """The weakest source used in the day, so a mixed day is not oversold.

    During the Phase 3 rollout a day may combine a real ETA with an estimate. A
    day is only as trustworthy as its least trustworthy leg, so report that one
    rather than the best one.
    """
    sources = {i.leg_from_previous.source for i in items if i.leg_from_previous}
    if "static_haversine" in sources or not sources:
        return "static_haversine"
    return "maps_api"


async def load_cached_legs(
    conn: DbConn,
    candidates: CandidateSet,
    *,
    provider: RoutingProvider | None = None,
) -> dict[tuple[UUID, UUID], TravelLeg]:
    """Cached legs worth reusing, given who is doing the measuring now.

    A cached row from a weaker source is ignored rather than reused: otherwise the
    memo table pins an itinerary to whatever provider happened to run first.
    """
    return await travel_store.load_legs(
        conn,
        [c.poi_id for c in candidates.all()],
        no_worse_than=provider.source if provider is not None else None,
    )


async def persist_computed_legs(conn: DbConn, resolver: LegResolver) -> int:
    for from_poi, to_poi, leg in resolver.computed:
        await travel_store.save_leg(conn, from_poi, to_poi, leg)
    return len(resolver.computed)
