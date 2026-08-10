"""The routing seam.

Distances, durations and stop ordering are NEVER the model's job — they come
from a provider behind this interface. Phase 1 ships a static estimator; Phase 3
adds a maps-API provider that satisfies the same protocol and writes rows to
`travel_estimates` under a different `source`, so both datasets coexist and the
swap is a config change.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Protocol

from tripplan.domain.models import GeoPoint, TravelLeg
from tripplan.domain.taxonomy import TravelSource

EARTH_RADIUS_KM = 6371.0088


def haversine_km(a: GeoPoint, b: GeoPoint) -> float:
    """Great-circle distance in km. Mirrors the SQL function in migration 001."""
    d_lat = radians(b.lat - a.lat)
    d_lon = radians(b.lon - a.lon)
    h = sin(d_lat / 2) ** 2 + cos(radians(a.lat)) * cos(radians(b.lat)) * sin(d_lon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * asin(sqrt(h))


class RoutingProvider(Protocol):
    """Computes a travel leg between two points."""

    @property
    def source(self) -> TravelSource: ...

    def leg(self, a: GeoPoint, b: GeoPoint) -> TravelLeg: ...
