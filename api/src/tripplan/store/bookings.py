"""Booking requests — Phase 4.

READ THIS BEFORE ADDING A "CONFIRM" PATH.

This module records what a traveller asked for. It does not book anything, and it
must not imply that it did. The reasons are data, not laziness:

* no stay in the seed set has a verified contact (`stays.yaml` leaves `contact`
  empty on purpose — a wrong number misdirects a real traveller)
* every seeded guide is an explicit placeholder, refused by `publish`
* there is no partner API, no inventory feed and no payment provider

So a request is created as `requested` with `sent_via = NULL`, and the UI reads
that NULL to say plainly that we could not deliver it. The database refuses to
call anything `sent` or `confirmed` without a channel
(`bookings_sent_needs_channel`), so the honest state cannot be skipped by
accident — only by someone deliberately writing a channel that does not exist.

What this DOES give the traveller: a durable record of the intent, with a snapshot
of exactly what they were shown, and everything they need to make the call
themselves. That is a real product, and it is the row a partner integration will
one day fulfil.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from tripplan.db import DbConn
from tripplan.observability.logging import get_logger

log = get_logger(__name__)

BookingKind = Literal["stay", "guide", "activity"]
BookingStatus = Literal["requested", "sent", "confirmed", "declined", "withdrawn"]

# Statuses that still need someone to act. A withdrawn or declined request is
# closed, so asking again is allowed — which is why the unique index is partial.
OPEN_STATUSES: tuple[BookingStatus, ...] = ("requested", "sent", "confirmed")


class BookingTarget(BaseModel):
    """What is being requested, resolved from the database.

    Built here rather than accepted from the client so a caller cannot invent a
    property, a price or a phone number — the same rule that governs itineraries.
    """

    kind: BookingKind
    poi_id: UUID | None = None
    guide_id: UUID | None = None
    name: str
    locality: str | None = None
    # Verbatim from the row. Empty is the normal case and the UI must say so
    # rather than rendering an empty block that looks like a loading failure.
    contact: dict[str, Any] = Field(default_factory=dict)
    price_note: str | None = None
    is_verified: bool = False
    # True when the row is a synthetic placeholder. Such rows never reach a
    # traveller via `publish`, but the flag travels with the snapshot so a
    # request made in local dev is identifiable forever.
    is_placeholder: bool = False


class Booking(BaseModel):
    """A recorded request, as read back."""

    id: UUID
    kind: BookingKind
    status: BookingStatus
    party_size: int
    check_in: date | None = None
    check_out: date | None = None
    note: str | None = None
    itinerary_id: UUID | None = None
    day_number: int | None = None
    target: BookingTarget
    # NULL means: recorded, but we could not hand it to anyone.
    sent_via: str | None = None
    created_at: str

    @property
    def deliverable(self) -> bool:
        """Whether we hold a channel that could carry this request."""
        return bool(self.target.contact)


async def resolve_target(
    conn: DbConn,
    *,
    kind: BookingKind,
    slug: str,
) -> BookingTarget | None:
    """Look up what a request points at, or None if we do not publish it.

    Only published rows are bookable. A draft POI is one we have not fact-checked,
    and inviting a traveller to book it would defeat the point of the publish gate.
    """
    if kind == "guide":
        row = await conn.fetchrow(
            """
            SELECT g.id, g.name, g.contact, g.is_verified, g.source,
                   r.name AS locality, g.day_rate_paise
            FROM guides g
            LEFT JOIN regions r ON r.id = g.region_id
            WHERE g.slug = $1 AND g.status = 'published'
            """,
            slug,
        )
        if row is None:
            return None
        rate = row["day_rate_paise"]
        return BookingTarget(
            kind="guide",
            guide_id=UUID(str(row["id"])),
            name=str(row["name"]),
            locality=row["locality"],
            contact=dict(row["contact"] or {}),
            price_note=f"₹{int(rate) // 100:,} per day" if rate else None,
            is_verified=bool(row["is_verified"]),
            is_placeholder=row["source"] == "placeholder",
        )

    row = await conn.fetchrow(
        """
        SELECT p.id, p.name, p.kind, p.source, (p.verified_at IS NOT NULL) AS is_verified,
               r.name AS locality,
               sd.contact AS stay_contact, sd.per_night_min_paise, sd.per_night_max_paise,
               ad.contact AS activity_contact, p.cost_min_paise, p.cost_max_paise
        FROM pois p
        LEFT JOIN regions r ON r.id = p.region_id
        LEFT JOIN stay_details sd ON sd.poi_id = p.id
        LEFT JOIN activity_details ad ON ad.poi_id = p.id
        WHERE p.slug = $1 AND p.status = 'published' AND p.kind = $2
        """,
        slug,
        kind,
    )
    if row is None:
        return None

    if kind == "stay":
        contact = dict(row["stay_contact"] or {})
        low, high = row["per_night_min_paise"], row["per_night_max_paise"]
        price = _price_note(low, high, suffix=" per night")
    else:
        contact = dict(row["activity_contact"] or {})
        price = _price_note(row["cost_min_paise"], row["cost_max_paise"], suffix=" per person")

    return BookingTarget(
        kind=kind,
        poi_id=UUID(str(row["id"])),
        name=str(row["name"]),
        locality=row["locality"],
        contact=contact,
        price_note=price,
        is_verified=bool(row["is_verified"]),
        is_placeholder=row["source"] == "placeholder",
    )


def _price_note(low: int | None, high: int | None, *, suffix: str) -> str | None:
    """A price range in rupees, or None. Integer paise in, never a float."""
    if low is None and high is None:
        return None
    lo = (low if low is not None else high) or 0
    hi = (high if high is not None else low) or 0
    if lo == hi:
        return f"₹{lo // 100:,}{suffix}"
    return f"₹{lo // 100:,}–{hi // 100:,}{suffix}"  # noqa: RUF001 — en dash is correct for a range


class BookingConflictError(Exception):
    """Raised when this session already has an open request for the same target."""


async def request_booking(
    conn: DbConn,
    *,
    session_token: str,
    target: BookingTarget,
    party_size: int,
    check_in: date | None = None,
    check_out: date | None = None,
    note: str | None = None,
    itinerary_id: UUID | None = None,
    day_number: int | None = None,
) -> UUID:
    """Record a request. Always `requested`; never `sent`, because we cannot send.

    Raises `BookingConflictError` when an open request for the same target already
    exists for this session — a double-click must not produce two identical rows.
    """
    existing = await conn.fetchval(
        """
        SELECT id FROM bookings
        WHERE session_token = $1
          AND kind = $2
          AND COALESCE(poi_id, guide_id) = $3
          AND COALESCE(itinerary_id, '00000000-0000-0000-0000-000000000000'::uuid)
              = COALESCE($4::uuid, '00000000-0000-0000-0000-000000000000'::uuid)
          AND status NOT IN ('withdrawn', 'declined')
        """,
        session_token,
        target.kind,
        target.poi_id or target.guide_id,
        itinerary_id,
    )
    if existing is not None:
        raise BookingConflictError(str(existing))

    booking_id = await conn.fetchval(
        """
        INSERT INTO bookings (
            session_token, kind, poi_id, guide_id, itinerary_id, day_number,
            party_size, check_in, check_out, note, target_snapshot
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        RETURNING id
        """,
        session_token,
        target.kind,
        target.poi_id,
        target.guide_id,
        itinerary_id,
        day_number,
        party_size,
        check_in,
        check_out,
        note,
        # The snapshot is the record of what we showed. Stored as the model dumps
        # it so a later contact change cannot rewrite history.
        target.model_dump(mode="json"),
    )
    log.info(
        "booking.requested",
        booking_id=str(booking_id),
        kind=target.kind,
        deliverable=bool(target.contact),
    )
    return UUID(str(booking_id))


async def list_bookings(conn: DbConn, *, session_token: str) -> list[Booking]:
    """This session's requests, newest first.

    Scoped by session token and nothing else. There are no accounts yet, so the
    token IS the identity — which also means a request cannot leak to another
    browser, and that is asserted by a test.
    """
    rows = await conn.fetch(
        """
        SELECT id, kind, status, party_size, check_in, check_out, note,
               itinerary_id, day_number, target_snapshot, sent_via, created_at
        FROM bookings
        WHERE session_token = $1
        ORDER BY created_at DESC
        """,
        session_token,
    )
    return [
        Booking(
            id=UUID(str(r["id"])),
            kind=r["kind"],
            status=r["status"],
            party_size=int(r["party_size"]),
            check_in=r["check_in"],
            check_out=r["check_out"],
            note=r["note"],
            itinerary_id=UUID(str(r["itinerary_id"])) if r["itinerary_id"] else None,
            day_number=r["day_number"],
            target=BookingTarget(**dict(r["target_snapshot"])),
            sent_via=r["sent_via"],
            created_at=r["created_at"].isoformat(),
        )
        for r in rows
    ]


async def withdraw(conn: DbConn, *, session_token: str, booking_id: UUID) -> bool:
    """Withdraw a request. Scoped by session so one browser cannot cancel another's."""
    result = await conn.execute(
        """
        UPDATE bookings SET status = 'withdrawn'
        WHERE id = $1 AND session_token = $2 AND status NOT IN ('withdrawn', 'declined')
        """,
        booking_id,
        session_token,
    )
    return result.endswith("1")
