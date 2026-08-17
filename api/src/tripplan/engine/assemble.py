"""Stage 5 — assemble the client-facing itinerary.

Every factual field on the output is written here, from the database row or the
routing provider. A composer contributed only: the trip title, the day titles,
the prose, and which candidate refs to use. If a coordinate, price, duration,
contact or time appears in the payload, it came from this module.

This is also where the itinerary gets honest about itself: warnings for long
travel days, missing beds, permits, and data that no human has verified yet.
"""

from __future__ import annotations

from datetime import UTC, datetime
from math import ceil

from tripplan.domain.models import (
    SCHEMA_VERSION,
    BriefSummary,
    Candidate,
    ItineraryDay,
    ItineraryItem,
    ItinerarySummary,
    ItineraryWarning,
    Money,
    RegionRef,
    ReturnLeg,
    StayCard,
    TagRef,
    TripBrief,
    WarningCode,
)
from tripplan.domain.models import Itinerary as ItineraryModel
from tripplan.domain.taxonomy import ComposerName
from tripplan.engine.routing import RoutedDay, RoutedPlan
from tripplan.engine.validate import ValidationResult
from tripplan.observability.logging import get_logger

log = get_logger(__name__)

# Used when a stay does not declare an occupancy. Two per room is the common
# case for the resorts and homestays in this district.
DEFAULT_OCCUPANCY = 2

# Past this, a stop is realistically shut or unlit (19:30 local).
LATE_FINISH_MINUTES = 19 * 60 + 30


def _parse_hhmm(value: str) -> int:
    hours, _, minutes = value.partition(":")
    return int(hours) * 60 + int(minutes or 0)


def _fmt_hhmm(total_minutes: int) -> str:
    # Clamped at the same day: an itinerary that spills past midnight is a
    # scheduling problem to warn about, not a time to render as "26:30".
    minutes = min(total_minutes, 23 * 60 + 59)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _stay_card(stay: Candidate) -> StayCard:
    detail = stay.detail
    per_night: Money | None = None
    low, high = detail.get("per_night_min_paise"), detail.get("per_night_max_paise")
    if low is not None or high is not None:
        per_night = Money(min_paise=int(low or high or 0), max_paise=int(high or low or 0))
    return StayCard(
        poi_id=stay.poi_id,
        slug=stay.slug,
        name=stay.name,
        stay_type=str(detail.get("stay_type", "other")),
        region=stay.region,
        per_night=per_night,
        point=stay.point,
        contact=dict(detail.get("contact") or {}),
        media=stay.media,
        meals_included=bool(detail.get("meals_included", False)),
        amenities=list(detail.get("amenities") or []),
    )


def _rooms_needed(stay: Candidate, party_size: int) -> int:
    occupancy = int(stay.detail.get("max_occupancy") or DEFAULT_OCCUPANCY)
    return max(1, ceil(party_size / max(occupancy, 1)))


def _day_cost(day: RoutedDay, party_size: int) -> tuple[int, int]:
    """(min, max) paise for one day: entry/activity costs plus the night's rooms."""
    low = high = 0
    for item in day.items:
        if item.candidate.cost is not None:
            # Entry fees and activity charges are per head.
            low += item.candidate.cost.min_paise * party_size
            high += item.candidate.cost.max_paise * party_size
    if day.stay is not None:
        card = _stay_card(day.stay)
        if card.per_night is not None:
            rooms = _rooms_needed(day.stay, party_size)
            low += card.per_night.min_paise * rooms
            high += card.per_night.max_paise * rooms
    return low, high


def assemble(
    plan: RoutedPlan,
    brief: TripBrief,
    *,
    title: str,
    narrative: str | None,
    composer: ComposerName,
    validation: ValidationResult,
    district: RegionRef,
    interests: list[TagRef],
    day_start_time: str,
    max_travel_minutes_per_day: int,
    unmet_interests: list[str] | None = None,
    day_geometry: dict[int, list[list[float]]] | None = None,
    candidate_set_hash: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> ItineraryModel:
    days: list[ItineraryDay] = []
    warnings: list[ItineraryWarning] = []
    cost_low = cost_high = 0
    unverified: set[str] = set()
    permits: dict[int, list[str]] = {}

    for day in plan.days:
        cursor = _parse_hhmm(day_start_time)
        items: list[ItineraryItem] = []

        for slot, routed in enumerate(day.items, start=1):
            candidate = routed.candidate
            if routed.leg_from_previous is not None:
                cursor += routed.leg_from_previous.duration_minutes

            items.append(
                ItineraryItem(
                    slot=slot,
                    kind=candidate.kind,
                    poi_id=candidate.poi_id,
                    name=candidate.name,
                    summary=candidate.summary,
                    region=candidate.region,
                    why_chosen=routed.why_chosen,
                    start_time_estimate=_fmt_hhmm(cursor),
                    duration_minutes=candidate.duration_minutes,
                    cost=candidate.cost,
                    point=candidate.point,
                    media=candidate.media,
                    detail=candidate.detail,
                    leg_from_previous=routed.leg_from_previous,
                    guides=candidate.guides,
                )
            )
            cursor += candidate.duration_minutes or 0

            if not candidate.is_verified:
                unverified.add(candidate.name)
            if candidate.detail.get("requires_permit"):
                permits.setdefault(day.day_number, []).append(candidate.name)

        if day.stay is not None and not day.stay.is_verified:
            unverified.add(day.stay.name)

        # A day that ends after dark is a scheduling failure, not a full day:
        # temples close, trails are unsafe, and viewpoints show nothing.
        if items and cursor > LATE_FINISH_MINUTES:
            warnings.append(
                ItineraryWarning(
                    code="late_finish",
                    day_number=day.day_number,
                    message=(
                        f"The last stop is not reached until about {_fmt_hhmm(cursor)}. "
                        "Expect it to be closed or dark; consider dropping a stop or "
                        "adding a day."
                    ),
                )
            )

        low, high = _day_cost(day, brief.party_size)
        cost_low += low
        cost_high += high

        days.append(
            ItineraryDay(
                day_number=day.day_number,
                title=day.title,
                narrative=day.narrative,
                travel=day.travel,
                stay=_stay_card(day.stay) if day.stay is not None else None,
                items=items,
                route=(day_geometry or {}).get(day.day_number, []),
            )
        )

        if day.travel and day.travel.duration_minutes > max_travel_minutes_per_day:
            hours = day.travel.duration_minutes / 60
            warnings.append(
                ItineraryWarning(
                    code="long_travel_day",
                    day_number=day.day_number,
                    message=(
                        f"About {hours:.1f} hours of driving, above the "
                        f"{max_travel_minutes_per_day // 60}-hour comfortable limit. "
                        "Estimates are straight-line based and optimistic for ghat roads."
                    ),
                )
            )

    # Advisory violations from stage 3 become client-visible warnings rather
    # than being dropped on the floor. The mapping is explicit because
    # "too little to do" and "too much to do" are opposite problems and must not
    # collapse into one code.
    advisory_codes: dict[str, WarningCode] = {
        "no_stay": "no_stay_available",
        "empty_day": "arrival_day",
        "over_time_budget": "packed_day",
    }
    for violation in validation.advisory:
        warnings.append(
            ItineraryWarning(
                code=advisory_codes.get(violation.code, "thin_day"),
                day_number=violation.day_number,
                message=violation.message,
            )
        )

    if unmet_interests:
        # The seasonal filter is the usual cause: asking for trekking in August
        # returns nothing, because the trails are closed. Saying so is the whole
        # point — an itinerary that silently drops a requested interest is
        # answering a different question.
        warnings.append(
            ItineraryWarning(
                code="interest_unmet",
                message=(
                    "No places matched: "
                    + ", ".join(sorted(unmet_interests))
                    + ". This is usually seasonal — many treks and falls are closed or "
                    "unsafe outside October to February. The plan covers your other "
                    "interests only."
                ),
            )
        )

    for day_number, names in sorted(permits.items()):
        warnings.append(
            ItineraryWarning(
                code="permit_required",
                day_number=day_number,
                message=(
                    "Needs a forest department permit booked in advance: "
                    + ", ".join(sorted(set(names)))
                ),
            )
        )

    if unverified:
        # Deliberately prominent. The Phase 1 dataset is hand-compiled and
        # published-but-unverified, and pretending otherwise would be the most
        # damaging thing this app could do.
        warnings.append(
            ItineraryWarning(
                code="unverified_data",
                message=(
                    f"{len(unverified)} entry/entries in this plan come from data that has "
                    "not been human-verified. Confirm opening times, prices and permits "
                    "before travelling."
                ),
            )
        )

    if composer == "deterministic":
        warnings.append(
            ItineraryWarning(
                code="fallback_composer",
                message=(
                    "Built by the deterministic composer, so it is routed sensibly but "
                    "not curated for pacing or theme."
                ),
            )
        )

    return_leg: ReturnLeg | None = None
    if plan.return_leg is not None:
        return_leg = ReturnLeg(
            from_poi_id=plan.return_from.poi_id if plan.return_from else None,
            to=brief.origin,
            distance_km=plan.return_leg.distance_km,
            duration_minutes=plan.return_leg.duration_minutes,
            source=plan.return_leg.source,
        )

    itinerary = ItineraryModel(
        schema_version=SCHEMA_VERSION,
        request_id=brief.request_id,
        generated_at=datetime.now(UTC),
        composer=composer,
        llm_provider=llm_provider,
        llm_model=llm_model,
        candidate_set_hash=candidate_set_hash,
        brief=BriefSummary(
            mode=brief.mode,
            interests=interests,
            days=brief.days,
            party_size=brief.party_size,
            budget_band=brief.budget_band,
            origin=brief.origin,
            district=district,
            # Carried through so a location-mode itinerary can say what it was
            # planned around. Without these the payload was indistinguishable
            # from a district plan that happened to pick the same stops.
            anchor=brief.anchor,
            radius_km=brief.radius_km,
        ),
        summary=ItinerarySummary(
            title=title,
            narrative=narrative,
            total_distance_km=plan.total_distance_km,
            total_travel_minutes=plan.total_travel_minutes,
            # Excludes fuel/transport and meals other than those a stay includes.
            estimated_cost=Money(min_paise=cost_low, max_paise=cost_high, per_person=False),
            warnings=warnings,
        ),
        days=days,
        return_leg=return_leg,
    )

    log.info(
        "engine.assemble",
        composer=composer,
        days=len(days),
        warnings=len(warnings),
        cost_min_inr=cost_low // 100,
        cost_max_inr=cost_high // 100,
    )
    return itinerary
