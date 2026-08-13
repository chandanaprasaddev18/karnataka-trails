"""Phase 3 routing: real road distances from OSRM.

WHY OSRM. It is open source, it runs on OpenStreetMap data, and the public demo
server needs no key and no billing account. That matters here beyond cost: a
routing provider you cannot run yourself is a provider you cannot test offline or
reproduce a year later. A commercial API stays possible — `travel_estimates.source`
is in the primary key precisely so a third source can land beside these two — but
nothing about this app requires one.

WHAT IT FIXES. The static estimator applied a flat 28 km/h to straight-line
distance times 1.35. For Bengaluru to Chikkamagaluru it produced 276 km and
9 h 50 m. The road is 249 km and takes about 3 h 10 m. Every itinerary was
therefore carrying a "long travel day" warning it had earned by arithmetic rather
than by geography, and days were being under-filled to leave room for driving that
does not take that long.

THE SHAPE OF THE INTEGRATION. `RoutingProvider.leg()` is synchronous and is
called from inside the routing stage, which is not a place to start making HTTP
requests one hop at a time. So this provider is warmed first: `warm()` asks OSRM's
**table** service for the whole distance matrix over the candidate set in one
request, and `leg()` then reads that matrix. It is one round trip per plan rather
than one per hop, and the engine's structure does not change.

WHEN OSRM IS UNREACHABLE, nothing fails. `leg()` falls through to the static
estimate and returns it labelled `static_haversine`, so a degraded plan says which
legs are guesses instead of passing them off as measurements. That is the same
principle as `itineraries.composer` recording the LLM fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from tripplan.domain.models import GeoPoint, TravelLeg
from tripplan.domain.taxonomy import TravelSource
from tripplan.observability.logging import get_logger
from tripplan.routing.static_provider import StaticEstimateProvider

log = get_logger(__name__)

# OSRM's public demo caps a table request; and a matrix is quadratic, so a large
# candidate set is worth splitting rather than sending whole. 90 keeps us inside
# the documented limit of 100 with room for the origin.
MAX_TABLE_COORDS = 90

# Coordinates are rounded before being used as a cache key. Five decimal places is
# about a metre — finer than any POI coordinate we hold, and coarse enough that
# floating-point noise cannot turn one place into two cache misses.
_KEY_PRECISION = 5


def _key(point: GeoPoint) -> tuple[float, float]:
    return (round(point.lat, _KEY_PRECISION), round(point.lon, _KEY_PRECISION))


@dataclass
class OsrmRoutingProvider:
    """Real road legs from OSRM, with the static estimator as a fallback.

    Stateful by design: `warm()` fills `_matrix`, `leg()` reads it. A provider
    that has not been warmed still works — every leg is simply a static estimate,
    honestly labelled.
    """

    base_url: str = "https://router.project-osrm.org"
    timeout_seconds: float = 20.0
    fallback: StaticEstimateProvider = field(default_factory=StaticEstimateProvider)

    _matrix: dict[tuple[tuple[float, float], tuple[float, float]], TravelLeg] = field(
        default_factory=dict, repr=False
    )
    # Counted so the pipeline can log how much of a plan is real. A number in a log
    # line beats a claim in a docstring.
    misses: int = 0

    @property
    def source(self) -> TravelSource:
        return "osrm"

    def leg(self, a: GeoPoint, b: GeoPoint) -> TravelLeg:
        if _key(a) == _key(b):
            return TravelLeg(distance_km=0.0, duration_minutes=0, source=self.source)
        hit = self._matrix.get((_key(a), _key(b)))
        if hit is not None:
            return hit
        # Not in the warmed matrix: either warm() was never called or OSRM failed.
        # Return the estimate under ITS OWN source, never as an OSRM measurement.
        self.misses += 1
        return self.fallback.leg(a, b)

    async def warm(self, points: list[GeoPoint]) -> int:
        """Fetch the full matrix over `points`. Returns the number of legs learned.

        Never raises. A routing provider that can take down itinerary generation
        when a free public service is slow is worse than one that degrades, and the
        degradation is visible in the payload because each leg carries its source.
        """
        unique: list[GeoPoint] = []
        seen: set[tuple[float, float]] = set()
        for point in points:
            if _key(point) not in seen:
                seen.add(_key(point))
                unique.append(point)

        if len(unique) < 2:
            return 0
        if len(unique) > MAX_TABLE_COORDS:
            # Truncation is logged rather than silent: the dropped points fall back
            # to estimates, and a reader of the logs should know why.
            log.warning(
                "routing.osrm.matrix_truncated",
                requested=len(unique),
                used=MAX_TABLE_COORDS,
            )
            unique = unique[:MAX_TABLE_COORDS]

        coords = ";".join(f"{p.lon:.6f},{p.lat:.6f}" for p in unique)
        url = f"{self.base_url}/table/v1/driving/{coords}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    url, params={"annotations": "duration,distance"}
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("routing.osrm.unavailable", error=str(exc), points=len(unique))
            return 0

        if body.get("code") != "Ok":
            log.warning("routing.osrm.rejected", code=body.get("code"))
            return 0

        durations = body.get("durations") or []
        distances = body.get("distances") or []
        learned = 0
        for i, from_point in enumerate(unique):
            for j, to_point in enumerate(unique):
                if i == j:
                    continue
                seconds = _cell(durations, i, j)
                metres = _cell(distances, i, j)
                if seconds is None or metres is None:
                    # OSRM returns null for a pair it cannot connect (an island, a
                    # coordinate it could not snap to a road). Leaving it out of the
                    # matrix makes it a miss, which falls back to an estimate.
                    continue
                self._matrix[(_key(from_point), _key(to_point))] = TravelLeg(
                    distance_km=round(metres / 1000, 2),
                    duration_minutes=round(seconds / 60),
                    source=self.source,
                )
                learned += 1

        log.info("routing.osrm.warmed", points=len(unique), legs=learned)
        return learned

    async def geometry(self, points: list[GeoPoint]) -> list[list[float]]:
        """The driven shape through `points`, as [[lat, lon], ...], or [] on failure.

        Used only for drawing. It comes from the route service rather than being
        interpolated between stops, because a straight line between two towns in
        the Ghats is not where the road goes and a map that pretends otherwise is
        worse than no map.
        """
        if len(points) < 2:
            return []

        coords = ";".join(f"{p.lon:.6f},{p.lat:.6f}" for p in points[:25])
        url = f"{self.base_url}/route/v1/driving/{coords}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    url, params={"overview": "simplified", "geometries": "geojson"}
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("routing.osrm.geometry_unavailable", error=str(exc))
            return []

        if body.get("code") != "Ok" or not body.get("routes"):
            return []

        line = body["routes"][0].get("geometry", {}).get("coordinates") or []
        # GeoJSON is [lon, lat]; everything in this codebase is [lat, lon]. Swapped
        # here, once, rather than in the renderer.
        return [[float(lat), float(lon)] for lon, lat in line]


def _cell(rows: list[list[float | None]], i: int, j: int) -> float | None:
    """One matrix cell, tolerating a short or ragged response."""
    if i >= len(rows):
        return None
    row = rows[i]
    if j >= len(row):
        return None
    value = row[j]
    return None if value is None else float(value)
