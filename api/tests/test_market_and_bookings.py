"""Phases 4 and 5 — booking requests, and the marketplace.

Both features are shaped by the same absence: we hold no verified commercial
contacts. So the tests here mostly assert what the app REFUSES to do, because that
is the part a future change could quietly break:

* a booking is never created as `sent` or `confirmed`, and the database refuses
  those states without a real channel
* a placeholder vendor never reaches a traveller, exactly as a placeholder guide
  never does
* one browser's requests never leak into another's

The happy paths are asserted too, but a regression there is loud. A regression in
the refusals would look like a working feature.
"""

from __future__ import annotations

from datetime import date

import asyncpg
import pytest
import pytest_asyncio

from tripplan.config import get_settings
from tripplan.store import bookings as booking_store
from tripplan.store import market as market_store
from tripplan.store.seed import (
    load_guides,
    load_interest_tags,
    load_pois,
    load_regions,
    load_specialities,
    load_vendors,
    publish,
)

DISTRICT = "chikkamagaluru"
# The only session tokens this suite may touch.
_SESSIONS = ("session-a", "session-b")


@pytest_asyncio.fixture
async def seeded(db: asyncpg.Connection) -> asyncpg.Connection:
    cfg = get_settings()
    await load_interest_tags(db, cfg.seeds_dir)
    await load_regions(db, cfg.seeds_dir)
    await load_pois(db, cfg.seeds_dir, DISTRICT)
    await load_guides(db, cfg.seeds_dir, DISTRICT)
    await load_vendors(db, cfg.seeds_dir, DISTRICT)
    await load_specialities(db, cfg.seeds_dir, DISTRICT)
    await publish(db, min_confidence=2)
    # Only this suite's own rows. `DELETE FROM bookings` also wiped whatever a
    # developer had saved in the browser, since the tests share the dev database —
    # a test that destroys the data you are looking at is worse than a dirty table.
    await db.execute(
        "DELETE FROM bookings WHERE session_token = ANY($1::text[])", list(_SESSIONS)
    )
    return db


async def _a_published_stay(conn: asyncpg.Connection) -> str:
    slug = await conn.fetchval(
        "SELECT slug FROM pois WHERE kind = 'stay' AND status = 'published' LIMIT 1"
    )
    assert slug is not None, "expected at least one published stay in the seed set"
    return str(slug)


# ---------------------------------------------------------------------------
# Phase 4 — what a booking request is, and is not
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_a_request_is_recorded_but_never_marked_sent(seeded: asyncpg.Connection) -> None:
    """The whole honesty claim of Phase 4, asserted.

    We cannot deliver a request — no stay in the seed set has a contact. So the row
    must be `requested` with no channel, and `deliverable` must be False. A future
    change that flipped this to look "sent" would be a lie the UI would faithfully
    repeat.
    """
    slug = await _a_published_stay(seeded)
    target = await booking_store.resolve_target(seeded, kind="stay", slug=slug)
    assert target is not None

    booking_id = await booking_store.request_booking(
        seeded,
        session_token="session-a",
        target=target,
        party_size=2,
        check_in=date(2026, 11, 14),
        check_out=date(2026, 11, 16),
    )

    row = await seeded.fetchrow(
        "SELECT status, sent_via, sent_at FROM bookings WHERE id = $1", booking_id
    )
    assert row is not None
    assert row["status"] == "requested"
    assert row["sent_via"] is None
    assert row["sent_at"] is None

    listed = await booking_store.list_bookings(seeded, session_token="session-a")
    assert len(listed) == 1
    assert listed[0].deliverable is False, (
        "no seeded stay has a contact, so nothing is deliverable — if this passes "
        "one day it must be because a real contact was added, not because the "
        "definition was loosened"
    )


@pytest.mark.integration
async def test_the_database_refuses_sent_without_a_channel(seeded: asyncpg.Connection) -> None:
    """`bookings_sent_needs_channel` is the backstop under the application rule.

    Application code can be edited; a CHECK constraint has to be migrated away
    deliberately. This asserts the constraint is really there.
    """
    slug = await _a_published_stay(seeded)
    target = await booking_store.resolve_target(seeded, kind="stay", slug=slug)
    assert target is not None
    booking_id = await booking_store.request_booking(
        seeded, session_token="session-a", target=target, party_size=2
    )

    with pytest.raises(asyncpg.IntegrityConstraintViolationError):
        await seeded.execute(
            "UPDATE bookings SET status = 'confirmed' WHERE id = $1", booking_id
        )

    # With a channel it is allowed — that is the path a partner integration takes.
    await seeded.execute(
        "UPDATE bookings SET status = 'sent', sent_via = 'phone' WHERE id = $1", booking_id
    )
    assert await seeded.fetchval("SELECT status FROM bookings WHERE id = $1", booking_id) == "sent"


@pytest.mark.integration
async def test_a_second_request_for_the_same_thing_is_refused(
    seeded: asyncpg.Connection,
) -> None:
    """A double-click must not produce two identical rows."""
    slug = await _a_published_stay(seeded)
    target = await booking_store.resolve_target(seeded, kind="stay", slug=slug)
    assert target is not None
    await booking_store.request_booking(
        seeded, session_token="session-a", target=target, party_size=2
    )

    with pytest.raises(booking_store.BookingConflictError):
        await booking_store.request_booking(
            seeded, session_token="session-a", target=target, party_size=2
        )

    # ...but withdrawing reopens the question, which is why the index is partial.
    listed = await booking_store.list_bookings(seeded, session_token="session-a")
    assert await booking_store.withdraw(
        seeded, session_token="session-a", booking_id=listed[0].id
    )
    again = await booking_store.request_booking(
        seeded, session_token="session-a", target=target, party_size=2
    )
    assert again is not None


@pytest.mark.integration
async def test_requests_do_not_leak_between_sessions(seeded: asyncpg.Connection) -> None:
    """There are no accounts, so the session token IS the identity boundary."""
    slug = await _a_published_stay(seeded)
    target = await booking_store.resolve_target(seeded, kind="stay", slug=slug)
    assert target is not None
    booking_id = await booking_store.request_booking(
        seeded, session_token="session-a", target=target, party_size=2
    )

    assert await booking_store.list_bookings(seeded, session_token="session-b") == []
    # And one browser cannot withdraw another's request.
    assert not await booking_store.withdraw(
        seeded, session_token="session-b", booking_id=booking_id
    )
    assert await seeded.fetchval("SELECT status FROM bookings WHERE id = $1", booking_id) == (
        "requested"
    )


@pytest.mark.integration
async def test_an_unpublished_target_cannot_be_requested(seeded: asyncpg.Connection) -> None:
    """Every seeded guide is a placeholder, so none is bookable.

    Same gate as the itinerary: if we have not checked it, a traveller cannot be
    pointed at it.
    """
    guide_slug = await seeded.fetchval("SELECT slug FROM guides LIMIT 1")
    assert guide_slug is not None
    assert await seeded.fetchval(
        "SELECT status FROM guides WHERE slug = $1", guide_slug
    ) == "draft"

    assert await booking_store.resolve_target(seeded, kind="guide", slug=str(guide_slug)) is None


@pytest.mark.integration
async def test_the_snapshot_does_not_change_when_the_row_does(
    seeded: asyncpg.Connection,
) -> None:
    """What the traveller was shown is part of the record.

    If a property's price changes next month, the request must still say what we
    told them — otherwise the audit trail is worthless.
    """
    slug = await _a_published_stay(seeded)
    target = await booking_store.resolve_target(seeded, kind="stay", slug=slug)
    assert target is not None
    original_price = target.price_note
    await booking_store.request_booking(
        seeded, session_token="session-a", target=target, party_size=2
    )

    await seeded.execute(
        """
        UPDATE stay_details SET per_night_min_paise = 99900000, per_night_max_paise = 99900000
        WHERE poi_id = (SELECT id FROM pois WHERE slug = $1)
        """,
        slug,
    )

    listed = await booking_store.list_bookings(seeded, session_token="session-a")
    assert listed[0].target.price_note == original_price


# ---------------------------------------------------------------------------
# Phase 5 — the marketplace lists places, not invented sellers
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_placeholder_vendors_are_never_published(seeded: asyncpg.Connection) -> None:
    """The strongest rule in the marketplace, for the strongest reason.

    A vendor is somebody a traveller might try to PAY. Every seeded vendor is
    explicitly synthetic, so `publish` must refuse all of them and the API must
    return nothing.
    """
    held = await seeded.fetchval("SELECT count(*) FROM vendors")
    assert int(held) > 0, "the fixture should load placeholder vendors"

    published = await seeded.fetchval("SELECT count(*) FROM vendors WHERE status = 'published'")
    assert int(published) == 0

    assert await market_store.products_for(seeded) == []


@pytest.mark.integration
async def test_a_product_cannot_outlive_its_vendors_gate(seeded: asyncpg.Connection) -> None:
    """A published product with a draft vendor is an offer with nobody behind it.

    Forced by hand here, because the publish gate would never create it: the join
    in `products_for` has to hold the line independently.
    """
    await seeded.execute("UPDATE products SET status = 'published'")
    assert await market_store.products_for(seeded) == [], (
        "a product whose vendor is not published must not be listed"
    )


@pytest.mark.integration
async def test_specialities_are_scoped_by_district_ancestry(
    seeded: asyncpg.Connection,
) -> None:
    """Asking for the district returns its taluks' specialities too.

    Same materialised-path trick the planning modes use, so a district query cannot
    miss a taluk-level row.
    """
    rows = await market_store.specialities(seeded, district_slug=DISTRICT)
    assert rows, "the seed set records specialities"

    regions = {r.region_slug for r in rows}
    assert DISTRICT in regions
    assert regions - {DISTRICT}, "expected taluk-level rows as well as the district's own"

    for row in rows:
        assert row.note.strip(), "a speciality with no note tells a traveller nothing"
        assert row.products == [], "no sellers are listed yet, and none may be implied"


@pytest.mark.integration
async def test_specialities_carry_provenance(seeded: asyncpg.Connection) -> None:
    """A claim about a place is only as good as where it came from."""
    rows = await market_store.specialities(seeded, district_slug=DISTRICT)
    for row in rows:
        assert row.source
        assert 1 <= row.data_confidence <= 5


@pytest.mark.integration
async def test_stats_admit_what_is_withheld(seeded: asyncpg.Connection) -> None:
    """The page's headline is a number, so the number must be honest.

    Reporting zero sellers while silently holding two vendor rows would make the
    empty marketplace look like missing data rather than a deliberate refusal.
    """
    stats = await market_store.stats(seeded)
    assert stats.specialities > 0
    assert stats.published_vendors == 0
    assert stats.withheld_vendors > 0
