"""Stage 0 — normalise a raw request into a validated TripBrief.

Everything downstream reads only the brief, so this is where user input stops
being user input: origin labels are resolved to coordinates, interests are
de-duplicated, and out-of-range numbers fail here with a readable message rather
than as a Postgres CHECK violation four stages later.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from tripplan.domain.models import OriginRef, TripBrief
from tripplan.domain.origins import resolve_origin
from tripplan.domain.taxonomy import PlanningMode


class BriefError(ValueError):
    """Raised when a request cannot be normalised into a valid brief."""


def build_brief(
    *,
    interests: list[str],
    district_slug: str,
    days: int,
    party_size: int,
    budget_band: int,
    origin_label: str,
    travel_month: int | None = None,
    mode: PlanningMode = "interest",
    request_id: UUID | None = None,
) -> TripBrief:
    cleaned = tuple(dict.fromkeys(i.strip().lower() for i in interests if i.strip()))
    if mode == "interest" and not cleaned:
        raise BriefError("at least one interest is required for the interest planning mode")

    try:
        origin = resolve_origin(origin_label)
    except ValueError as exc:  # UnknownOriginError
        raise BriefError(str(exc)) from exc

    # Default to the current month, but keep it an explicit field so a plan is
    # reproducible and the seasonal filter is testable.
    month = travel_month if travel_month is not None else datetime.now(UTC).month

    try:
        return TripBrief(
            request_id=request_id,
            mode=mode,
            tag_slugs=cleaned,
            district_slug=district_slug,
            days=days,
            party_size=party_size,
            budget_band=budget_band,
            origin=OriginRef.from_origin(origin),
            travel_month=month,
        )
    except ValueError as exc:
        raise BriefError(str(exc)) from exc
