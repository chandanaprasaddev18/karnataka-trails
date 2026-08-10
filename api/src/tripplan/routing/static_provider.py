"""Phase 1 routing: a static estimate, honestly labelled.

This is a PLACEHOLDER and every leg it produces says so via
``source='static_haversine'``, all the way out to the client. It applies a road
factor to straight-line distance and a flat average speed.

Its known weaknesses, which are exactly why Phase 3 replaces it rather than
tuning it:

* a ghat road with 40 hairpins and a flat highway get the same treatment
* it cannot know a road is closed, one-way, or seasonal
* it has no notion of a route that must detour around a ridge, which in this
  district can double a journey that looks short in a straight line

Because of the last point, estimates here are optimistic. The engine compensates
by flagging days over the travel budget rather than by inventing a fudge factor
that would be indistinguishable from real data.
"""

from __future__ import annotations

from dataclasses import dataclass

from tripplan.domain.models import GeoPoint, TravelLeg
from tripplan.domain.taxonomy import TravelSource
from tripplan.routing.base import haversine_km


@dataclass(frozen=True)
class StaticEstimateProvider:
    """Straight-line distance x road factor, at a flat average speed."""

    road_factor: float = 1.35
    avg_speed_kmh: float = 28.0

    @property
    def source(self) -> TravelSource:
        return "static_haversine"

    def leg(self, a: GeoPoint, b: GeoPoint) -> TravelLeg:
        straight = haversine_km(a, b)
        road_km = round(straight * self.road_factor, 2)
        minutes = round((road_km / self.avg_speed_kmh) * 60) if road_km else 0
        return TravelLeg(distance_km=road_km, duration_minutes=minutes, source=self.source)
