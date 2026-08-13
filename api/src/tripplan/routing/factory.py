"""Which routing provider to use.

Mirrors `llm/factory.py`: the choice is one config value, the rest of the engine
sees only the `RoutingProvider` protocol, and nothing above this line knows
whether distances came from a road network or from trigonometry.
"""

from __future__ import annotations

from typing import assert_never

from tripplan.config import Settings
from tripplan.routing.base import RoutingProvider
from tripplan.routing.osrm import OsrmRoutingProvider
from tripplan.routing.static_provider import StaticEstimateProvider


def build_provider(settings: Settings) -> RoutingProvider:
    """The configured provider, ready to use (but not yet warmed)."""
    name = settings.routing.provider
    fallback = StaticEstimateProvider(
        road_factor=settings.routing.road_factor,
        avg_speed_kmh=settings.routing.avg_speed_kmh,
    )
    if name == "static":
        return fallback
    if name == "osrm":
        return OsrmRoutingProvider(
            base_url=settings.routing.osrm_base_url,
            timeout_seconds=settings.routing.osrm_timeout_seconds,
            fallback=fallback,
        )
    assert_never(name)
