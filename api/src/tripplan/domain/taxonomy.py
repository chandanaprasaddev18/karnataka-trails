"""Taxonomy and geography vocabulary.

These literals mirror the CHECK constraints in migration 001. Keeping them here
means a bad value fails in Pydantic with a clear message rather than as a
Postgres constraint violation three layers down.
"""

from __future__ import annotations

from typing import Literal

TagKind = Literal["interest", "terrain", "audience", "season", "product_category"]
RegionKind = Literal["state", "district", "taluk", "locality"]
PoiKind = Literal["place", "stay", "activity"]
PoiStatus = Literal["draft", "published", "archived"]
PlanningMode = Literal["interest", "location", "district"]
ComposerName = Literal["llm", "deterministic"]
TravelSource = Literal["static_haversine", "maps_api"]

PlaceType = Literal[
    "viewpoint",
    "temple",
    "waterfall",
    "fort",
    "lake",
    "trail",
    "museum",
    "garden",
    "wildlife",
    "town",
    "other",
]
StayType = Literal["resort", "homestay", "hotel", "campsite", "guesthouse", "other"]
ActivityType = Literal[
    "trek",
    "rafting",
    "safari",
    "offroad",
    "coffee_tour",
    "birding",
    "cycling",
    "kayaking",
    "workshop",
    "other",
]
TimeOfDay = Literal["sunrise", "morning", "afternoon", "sunset", "evening", "any"]

# Routing sources in preference order: the engine uses the first one that has a
# row for a given pair. Phase 3 ships `maps_api` and it wins automatically,
# without a data migration or a config change per pair.
TRAVEL_SOURCE_PREFERENCE: tuple[TravelSource, ...] = ("maps_api", "static_haversine")
