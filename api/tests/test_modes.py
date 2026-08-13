"""Phase 2 planning modes.

The product claim is that all three modes are ONE engine with one clause swapped
in retrieval. These tests pin the part of that claim which could rot silently:
each mode's scope is genuinely different, and each mode's scope survives the trip
through the database that the worker depends on.

Why that last one has its own test: the API resolves an anchor, the worker
re-reads it, and if the anchor is ever dropped in between, a "plan around
Mudigere" request quietly becomes "plan the whole district" — a wrong itinerary
that looks entirely successful. That exact failure shape already happened once
with `travel_month` (fixed in migration 003), so it gets a guard rather than a
comment.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from tripplan.config import get_settings
from tripplan.domain.models import AnchorRef, GeoPoint, TripBrief
from tripplan.domain.taxonomy import PoiKind
from tripplan.engine.brief import BriefError, build_brief
from tripplan.routing.base import haversine_km
from tripplan.store import pois as poi_store
from tripplan.store.itineraries import create_request, load_brief
from tripplan.store.seed import load_interest_tags, load_pois, load_regions, publish

DISTRICT = "chikkamagaluru"
PEAK_MONTH = 12  # in season, so the seasonal filter is not the thing under test


@pytest_asyncio.fixture
async def seeded(db: asyncpg.Connection) -> asyncpg.Connection:
    cfg = get_settings()
    await load_interest_tags(db, cfg.seeds_dir)
    await load_regions(db, cfg.seeds_dir)
    await load_pois(db, cfg.seeds_dir, DISTRICT)
    await publish(db, min_confidence=2)
    return db


async def _anchor(conn: asyncpg.Connection, slug: str) -> tuple[AnchorRef, str]:
    resolved = await poi_store.resolve_anchor(conn, slug)
    assert resolved is not None, f"expected to resolve anchor {slug!r}"
    anchor, district = resolved
    return anchor, district


def _brief(**overrides: Any) -> TripBrief:
    base: dict[str, Any] = {
        "interests": [],
        "district_slug": DISTRICT,
        "days": 2,
        "party_size": 2,
        "budget_band": 5,
        "origin_label": "Bengaluru",
        "travel_month": PEAK_MONTH,
    }
    base.update(overrides)
    return build_brief(**base)


# ---------------------------------------------------------------------------
# what each mode's scope actually means
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_district_mode_ignores_tags_and_interest_mode_does_not(
    seeded: asyncpg.Connection,
) -> None:
    """District mode is about scope, interest mode is about taste.

    A single narrow interest must return fewer places than the whole district,
    and district mode must return places NOBODY tagged with that interest — that
    is the difference between "everything here" and "everything matching this".
    """
    cfg = get_settings()
    narrow = _brief(mode="interest", interests=["spiritual"])
    whole = _brief(mode="district")

    narrow_places = await poi_store.fetch_candidates(
        seeded, narrow, "place", cfg.retrieval.max_places
    )
    whole_places = await poi_store.fetch_candidates(
        seeded, whole, "place", cfg.retrieval.max_places
    )

    assert narrow_places, "the seed set has spiritual places"
    assert len(whole_places) > len(narrow_places), "district mode must not be filtered by interest"
    untagged_for_spiritual = [c for c in whole_places if "spiritual" not in c.tags]
    assert untagged_for_spiritual, "district mode should surface places outside the tag"


@pytest.mark.integration
async def test_location_mode_returns_only_what_is_inside_the_radius(
    seeded: asyncpg.Connection,
) -> None:
    """The radius is a hard boundary, and it is measured from the anchor.

    Checked against the Python haversine rather than trusting the SQL one: if the
    two ever disagree, a location trip would include stops the UI says are within
    reach and are not.
    """
    anchor, district = await _anchor(seeded, "mudigere")
    radius = 30
    brief = _brief(mode="location", anchor=anchor, radius_km=radius, district_slug=district)

    kinds: tuple[PoiKind, ...] = ("place", "activity", "stay")
    for kind in kinds:
        found = await poi_store.fetch_candidates(seeded, brief, kind, 50)
        for candidate in found:
            distance = haversine_km(anchor.point, candidate.point)
            assert distance <= radius + 0.01, (
                f"{candidate.name} is {distance:.1f} km from the anchor, outside {radius} km"
            )


@pytest.mark.integration
async def test_a_wider_radius_never_loses_a_place(seeded: asyncpg.Connection) -> None:
    """Widening the search must be monotonic.

    A user who finds too little and drags the slider right expects strictly more,
    never a different set. This would break the moment ranking sneaked into the
    scope clause.
    """
    anchor, district = await _anchor(seeded, "mudigere")
    near = _brief(mode="location", anchor=anchor, radius_km=20, district_slug=district)
    far = _brief(mode="location", anchor=anchor, radius_km=80, district_slug=district)

    near_names = {c.name for c in await poi_store.fetch_candidates(seeded, near, "place", 50)}
    far_names = {c.name for c in await poi_store.fetch_candidates(seeded, far, "place", 50)}

    assert near_names <= far_names, "widening the radius dropped a place it had already found"
    assert len(far_names) > len(near_names), "80 km should reach more than 20 km in this district"


# ---------------------------------------------------------------------------
# the anchor must survive the database round trip
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_anchor_and_radius_survive_the_worker_round_trip(
    seeded: asyncpg.Connection,
) -> None:
    """A location request re-read by the worker is still a location request.

    If the anchor were dropped on the way through `trip_requests`, the worker
    would plan the entire district and report success. Nothing else in the system
    would notice, which is why this is asserted rather than assumed.
    """
    anchor, district = await _anchor(seeded, "mullayanagiri")
    original = _brief(mode="location", anchor=anchor, radius_km=45, district_slug=district)

    request_id = await create_request(seeded, original, session_token="test")
    reloaded = await load_brief(seeded, request_id)

    assert reloaded is not None
    assert reloaded.mode == "location"
    assert reloaded.radius_km == 45
    assert reloaded.anchor is not None
    assert reloaded.anchor.slug == "mullayanagiri"
    assert reloaded.anchor.kind == "poi"
    assert reloaded.anchor.point.lat == pytest.approx(anchor.point.lat)
    assert reloaded.anchor.point.lon == pytest.approx(anchor.point.lon)


@pytest.mark.integration
async def test_a_poi_anchor_keeps_its_foreign_key(seeded: asyncpg.Connection) -> None:
    """When the anchor is a POI, the audit trail keeps the real FK, not just a point."""
    anchor, district = await _anchor(seeded, "mullayanagiri")
    brief = _brief(mode="location", anchor=anchor, radius_km=45, district_slug=district)

    request_id = await create_request(seeded, brief, session_token="test")
    linked = await seeded.fetchval(
        """
        SELECT p.slug FROM trip_requests tr
        JOIN pois p ON p.id = tr.anchor_poi_id
        WHERE tr.id = $1
        """,
        request_id,
    )
    assert linked == "mullayanagiri"


@pytest.mark.integration
async def test_a_region_anchor_is_stored_without_inventing_a_poi(
    seeded: asyncpg.Connection,
) -> None:
    """A town is a legitimate anchor and must NOT create a POI row to be one."""
    anchor, district = await _anchor(seeded, "mudigere")
    assert anchor.kind == "region"

    brief = _brief(mode="location", anchor=anchor, radius_km=45, district_slug=district)
    request_id = await create_request(seeded, brief, session_token="test")

    row = await seeded.fetchrow(
        "SELECT anchor_poi_id, anchor_label, anchor_lat FROM trip_requests WHERE id = $1",
        request_id,
    )
    assert row is not None
    assert row["anchor_poi_id"] is None, "a region anchor must not be forced through a POI FK"
    assert row["anchor_label"] == "Mudigere"
    assert row["anchor_lat"] is not None


# ---------------------------------------------------------------------------
# what each mode requires of the caller
# ---------------------------------------------------------------------------


def test_interest_mode_still_requires_an_interest() -> None:
    with pytest.raises(BriefError, match="at least one interest"):
        _brief(mode="interest", interests=[])


def test_location_mode_requires_an_anchor() -> None:
    with pytest.raises(BriefError, match="anchor"):
        _brief(mode="location")


def test_an_anchor_is_rejected_in_the_modes_that_have_no_use_for_one() -> None:
    """Silently ignoring it would leave the user thinking their radius applied."""
    anchor = AnchorRef(
        kind="region", slug="mudigere", label="Mudigere", point=GeoPoint(lat=13.1, lon=75.6)
    )
    with pytest.raises(BriefError, match="meaningless"):
        _brief(mode="district", anchor=anchor)


def test_location_mode_defaults_to_a_usable_radius() -> None:
    """No radius must not mean no results; it means the default."""
    anchor = AnchorRef(
        kind="region", slug="mudigere", label="Mudigere", point=GeoPoint(lat=13.1, lon=75.6)
    )
    brief = _brief(mode="location", anchor=anchor)
    assert brief.radius_km is not None and brief.radius_km >= 25


# ---------------------------------------------------------------------------
# diagnosis, per mode
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_a_tiny_radius_is_diagnosed_as_a_radius_problem(
    seeded: asyncpg.Connection,
) -> None:
    """Not as "seed the database", which is unactionable and false here."""
    anchor, district = await _anchor(seeded, "mudigere")
    brief = _brief(mode="location", anchor=anchor, radius_km=5, district_slug=district)

    verdict = await poi_store.feasibility(seeded, brief)

    assert not verdict.ok
    assert verdict.reason == "nothing_in_radius"
    assert verdict.suggested_radius_km and verdict.suggested_radius_km > 5
    assert "radius" in verdict.explain()


@pytest.mark.integration
async def test_district_mode_out_of_season_still_suggests_months(
    seeded: asyncpg.Connection,
) -> None:
    """The suggestion path must work when there are no interests to reason from.

    District mode has no tags, so the "which months would work" query has to ask
    the question without a tag filter — otherwise it returns nothing and the user
    is told only that their trip is impossible.
    """
    brief = _brief(mode="district", travel_month=7, budget_band=1)
    verdict = await poi_store.feasibility(seeded, brief)

    if not verdict.ok:
        assert verdict.suggested_months or verdict.reason == "budget_too_low", (
            f"{verdict.reason} left the user with no alternative to try"
        )


# ---------------------------------------------------------------------------
# provenance must name the right measurer
# ---------------------------------------------------------------------------


def test_a_day_reports_its_weakest_leg_and_names_the_real_source() -> None:
    """A mixed day is only as good as its worst leg, and the label must be true.

    The old implementation returned the literal "maps_api" for anything that was
    not the static placeholder, so an OSRM-measured day claimed to come from a
    commercial maps API we do not use. Provenance that names the wrong provider is
    worse than no provenance at all.
    """
    from tripplan.engine.routing import _weakest

    assert _weakest({"osrm"}) == "osrm"
    assert _weakest({"osrm", "static_haversine"}) == "static_haversine"
    assert _weakest({"maps_api", "osrm"}) == "osrm"
    # An empty day has measured nothing, so it must not claim to have measured.
    assert _weakest(set()) == "static_haversine"


def test_the_composer_measures_between_real_places() -> None:
    """The approach drive must be quoted between coordinates a router knows.

    A cluster centroid is the average of several points, so it sits nowhere a road
    goes: a routing provider has no measurement for it and silently falls back to
    a straight-line estimate. That produced a day whose legs measured 3h15m while
    its own narrative called it a 9h52m drive, and — worse — reserved the larger
    number from the day's budget, leaving day 1 with no stops at all.
    """
    from tripplan.domain.models import Candidate, GeoPoint, RegionRef
    from tripplan.engine.compose_greedy import _anchor_point, _Cluster

    def _candidate(name: str, lat: float, lon: float) -> Candidate:
        return Candidate(
            ref=f"P{name}",
            poi_id=uuid4(),
            kind="place",
            name=name,
            summary="",
            region=RegionRef(slug="r", name="R"),
            point=GeoPoint(lat=lat, lon=lon),
        )

    cluster = _Cluster(
        region_slug="r",
        region_name="R",
        items=[
            _candidate("west", 13.0, 75.0),
            _candidate("middle", 13.1, 75.1),
            _candidate("east", 13.2, 75.2),
        ],
    )

    anchor = _anchor_point(cluster)
    assert (anchor.lat, anchor.lon) in {(13.0, 75.0), (13.1, 75.1), (13.2, 75.2)}, (
        "the anchor must be one of the cluster's real places, not their average"
    )
    assert (anchor.lat, anchor.lon) == (13.1, 75.1), "expected the member nearest the centroid"
