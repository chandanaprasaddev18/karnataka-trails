"""Origin resolution: a trip's starting point.

Phase 1 accepts a label and looks it up in this small table of realistic start
cities. It is deliberately NOT a database table: origins are not curated
content, they are user input, and Phase 3's maps integration replaces this
lookup with real geocoding. Encoding it as a dict keeps that swap to one
function rather than a schema change.

An unknown label is an error the caller must handle, not a silent (0, 0).
"""

from __future__ import annotations

from typing import NamedTuple


class Origin(NamedTuple):
    label: str
    lat: float
    lon: float


# Common start points for a Karnataka trip. Coordinates are city centres.
KNOWN_ORIGINS: dict[str, Origin] = {
    "bengaluru": Origin("Bengaluru", 12.9716, 77.5946),
    "mysuru": Origin("Mysuru", 12.2958, 76.6394),
    "mangaluru": Origin("Mangaluru", 12.9141, 74.8560),
    "hassan": Origin("Hassan", 13.0072, 76.0962),
    "shivamogga": Origin("Shivamogga", 13.9299, 75.5681),
    "hubballi": Origin("Hubballi", 15.3647, 75.1240),
    "udupi": Origin("Udupi", 13.3409, 74.7421),
    "chikkamagaluru": Origin("Chikkamagaluru", 13.3161, 75.7720),
}

# Accept the common alternate spellings users will actually type.
_ALIASES: dict[str, str] = {
    "bangalore": "bengaluru",
    "blr": "bengaluru",
    "mysore": "mysuru",
    "mangalore": "mangaluru",
    "hubli": "hubballi",
    "shimoga": "shivamogga",
    "chikmagalur": "chikkamagaluru",
    "chickmagaluru": "chikkamagaluru",
}


class UnknownOriginError(ValueError):
    """Raised when an origin label cannot be resolved to coordinates."""


def resolve_origin(label: str) -> Origin:
    """Resolve a user-supplied origin label to coordinates.

    Raises:
        UnknownOriginError: if the label is not a known start point.
    """
    key = label.strip().lower().replace(" ", "")
    key = _ALIASES.get(key, key)
    origin = KNOWN_ORIGINS.get(key)
    if origin is None:
        known = ", ".join(sorted(o.label for o in KNOWN_ORIGINS.values()))
        raise UnknownOriginError(
            f"unknown origin {label!r}. Phase 1 supports: {known}. "
            "Real geocoding arrives with the maps integration in Phase 3."
        )
    return origin
