"""Deterministic composer — geography-first, no model involved.

This exists for two reasons, and the second is the more valuable one:

1. **Fallback.** If the LLM is unavailable, returns an invalid selection, or
   fails its repair round-trip, the engine still produces a complete, valid
   itinerary. Degradation is visible in `itineraries.composer`, never silent.
2. **Baseline.** It is the control in the comparison "is the LLM actually
   earning its place?". Run the same brief through both and read them side by
   side; the difference is exactly the model's contribution. Without a baseline,
   an LLM itinerary only ever looks impressive because there is nothing to
   compare it to.

The strategy is deliberately simple and legible: cluster candidates by taluk,
visit clusters in a nearest-neighbour order from the origin, and fill each day
from its cluster by interest-match weight until the day's time budget is spent.
It optimises for a sane route and no wasted driving. What it cannot do is
pacing, thematic coherence, or knowing that two temples in a row is one temple
too many — which is precisely the judgement the LLM is being asked for.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tripplan.domain.models import (
    Candidate,
    CandidateSet,
    DraftDay,
    DraftItem,
    DraftItinerary,
    GeoPoint,
    TripBrief,
)
from tripplan.observability.logging import get_logger
from tripplan.routing.base import haversine_km

log = get_logger(__name__)

# Used when a POI has no `typical_duration_minutes`. The seed report flags such
# rows, so this is a safety net rather than an expected path.
DEFAULT_ITEM_MINUTES = 90


@dataclass
class _Cluster:
    """Candidates grouped by taluk — the unit a day is built around."""

    region_slug: str
    region_name: str
    items: list[Candidate] = field(default_factory=list)

    @property
    def centroid(self) -> GeoPoint:
        lat = sum(c.point.lat for c in self.items) / len(self.items)
        lon = sum(c.point.lon for c in self.items) / len(self.items)
        return GeoPoint(lat=lat, lon=lon)

    @property
    def score(self) -> int:
        """Cluster strength: how well its best few items match the interests."""
        top = sorted((c.match_weight for c in self.items), reverse=True)[:3]
        return sum(top)


def _cluster(candidates: CandidateSet) -> list[_Cluster]:
    grouped: dict[str, _Cluster] = {}
    for candidate in [*candidates.places, *candidates.activities]:
        cluster = grouped.setdefault(
            candidate.region.slug,
            _Cluster(region_slug=candidate.region.slug, region_name=candidate.region.name),
        )
        cluster.items.append(candidate)
    return [c for c in grouped.values() if c.items]


def _order_clusters(clusters: list[_Cluster], origin: GeoPoint, days: int) -> list[_Cluster]:
    """Nearest-neighbour tour from the origin, biased toward stronger clusters.

    Strength decides *which* clusters make the cut; geography decides the order
    they are visited in. Doing it the other way round produces itineraries that
    zig-zag across the district to reach a marginally better viewpoint.
    """
    chosen = sorted(clusters, key=lambda c: c.score, reverse=True)[:days]

    ordered: list[_Cluster] = []
    cursor = origin
    remaining = list(chosen)
    while remaining:
        nearest = min(remaining, key=lambda c: haversine_km(cursor, c.centroid))
        ordered.append(nearest)
        remaining.remove(nearest)
        cursor = nearest.centroid
    return ordered


def _nearest_stay(stays: list[Candidate], at: GeoPoint) -> Candidate | None:
    """Closest available stay to a day's centre of gravity.

    Stays are not filtered by interest (see store/pois.py), so this is purely a
    logistics decision: sleep as close as possible to where the day ends.
    """
    if not stays:
        return None
    return min(stays, key=lambda s: haversine_km(at, s.point))


def compose(
    brief: TripBrief,
    candidates: CandidateSet,
    *,
    day_activity_minutes: int,
    travel_minutes: Callable[[GeoPoint, GeoPoint], int] | None = None,
) -> DraftItinerary:
    """Build a complete draft itinerary without a model.

    `travel_minutes` lets the composer budget a day against its approach drive.
    Without it, day 1 of a trip starting 250 km away gets a full activity budget
    it cannot possibly spend, and stops end up scheduled after dark.
    """
    estimate = travel_minutes or (lambda a, b: 0)
    clusters = _cluster(candidates)
    if not clusters:
        return DraftItinerary(
            title=f"{brief.days}-day trip in {brief.district_slug}",
            narrative="No candidate places matched this brief.",
            days=[],
        )

    origin_point = GeoPoint(lat=brief.origin.lat, lon=brief.origin.lon)
    ordered = _order_clusters(clusters, origin_point, brief.days)

    used: set[str] = set()
    days: list[DraftDay] = []

    for day_number in range(1, brief.days + 1):
        # More days than clusters: cycle back through them. The `used` set keeps
        # a second visit to the same taluk from repeating the same places.
        cluster = ordered[(day_number - 1) % len(ordered)]

        # Reserve the approach drive out of the day's budget. On day 1 that is
        # the run from the origin; later days start from the previous cluster.
        #
        # Measured between REAL PLACES, not between cluster centroids. A centroid
        # is the average of several points and therefore somewhere no road goes: a
        # routing provider has no measurement for it and has to fall back to a
        # straight-line estimate. That is how a day whose legs measured 3h15m came
        # to describe itself as a "9h 52m" drive in its own narrative — the same
        # provider, asked about a place that does not exist, gave a very different
        # answer. Asking about the stop the traveller will actually drive to keeps
        # the prose and the routing stage in agreement.
        previous = ordered[(day_number - 2) % len(ordered)]
        approach = estimate(
            origin_point if day_number == 1 else _anchor_point(previous),
            _anchor_point(cluster),
        )
        budget = max(0, day_activity_minutes - approach)

        pool = sorted(
            (c for c in cluster.items if c.ref not in used or c.is_repeatable),
            key=lambda c: (-c.match_weight, -c.data_confidence, c.name),
        )

        picked: list[Candidate] = []
        spent = 0
        for candidate in pool:
            cost = candidate.duration_minutes or DEFAULT_ITEM_MINUTES
            if spent + cost > budget:
                # No room left today. An arrival day legitimately ends with zero
                # stops; validation treats that as advisory when a bed exists.
                continue
            picked.append(candidate)
            spent += cost
            if not candidate.is_repeatable:
                used.add(candidate.ref)
            if spent >= budget:
                break

        # Order the day's stops by proximity, starting from whatever the day
        # begins at. Stage 4 re-does this against real leg data; doing it here
        # too keeps the *baseline* itinerary sensible on its own.
        picked = _nearest_neighbour(picked, origin_point if day_number == 1 else cluster.centroid)

        day_centre = (
            GeoPoint(
                lat=sum(c.point.lat for c in picked) / len(picked),
                lon=sum(c.point.lon for c in picked) / len(picked),
            )
            if picked
            else cluster.centroid
        )
        stay = _nearest_stay(candidates.stays, day_centre)

        days.append(
            DraftDay(
                day_number=day_number,
                title=f"Day {day_number} — {cluster.region_name}",
                narrative=(
                    f"Travel day: about {_hhmm(approach)} to reach "
                    f"{cluster.region_name}, then overnight."
                    if not picked
                    else (
                        f"{len(picked)} {'stop' if len(picked) == 1 else 'stops'} around "
                        f"{cluster.region_name}, about {_hhmm(spent)} of activity."
                    )
                ),
                stay_ref=stay.ref if stay else None,
                items=[DraftItem(ref=c.ref, why_chosen=_why(c, brief)) for c in picked],
            )
        )

    log.info(
        "engine.compose.deterministic",
        days=len(days),
        items=sum(len(d.items) for d in days),
        clusters=len(ordered),
    )

    return DraftItinerary(
        title=(
            f"{brief.days} days around "
            + ", ".join(dict.fromkeys(c.region_name for c in ordered[: brief.days]))
        ),
        narrative=(
            "Built by the deterministic composer: clusters visited in "
            "nearest-neighbour order from the origin, each day filled by "
            "interest match within the day's time budget."
        ),
        days=days,
    )


def _anchor_point(cluster: _Cluster) -> GeoPoint:
    """A real place standing in for the cluster: the member nearest its centroid.

    Used wherever a distance must be *measured* rather than merely compared, so
    the number comes from a coordinate the routing provider actually knows.
    """
    return min(cluster.items, key=lambda c: haversine_km(cluster.centroid, c.point)).point


def _hhmm(minutes: int) -> str:
    """Human duration. This prose is user-facing, so "5h" beats "5h 00m"."""
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours}h {rest}m"
    if hours:
        return f"{hours}h"
    return f"{rest}m"


def _why(candidate: Candidate, brief: TripBrief) -> str:
    """A factual, non-persuasive rationale.

    The deterministic composer states which requested interest a stop satisfies
    and nothing more. Any prose beyond that is the LLM's job, and pretending
    otherwise would blur the baseline comparison.
    """
    matched = [t for t in candidate.tags if t in brief.tag_slugs]
    return f"Matches {', '.join(matched)}." if matched else "Matches the brief."


def _nearest_neighbour(items: list[Candidate], start: GeoPoint) -> list[Candidate]:
    if len(items) < 3:
        return items
    remaining = list(items)
    ordered: list[Candidate] = []
    cursor = start
    while remaining:
        nearest = min(remaining, key=lambda c: haversine_km(cursor, c.point))
        ordered.append(nearest)
        remaining.remove(nearest)
        cursor = nearest.point
    return ordered
