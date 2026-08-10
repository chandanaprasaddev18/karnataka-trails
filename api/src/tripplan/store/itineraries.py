"""Persistence for trip requests and generated itineraries."""

from __future__ import annotations

import json
from uuid import UUID

from tripplan.db import DbConn
from tripplan.domain.models import Itinerary, OriginRef, TripBrief
from tripplan.observability.logging import get_logger

log = get_logger(__name__)


async def create_request(conn: DbConn, brief: TripBrief, *, session_token: str) -> UUID:
    """Persist the brief as an immutable trip_requests row."""
    region_id = await conn.fetchval("SELECT id FROM regions WHERE slug = $1", brief.district_slug)
    tag_ids = [
        int(r["id"])
        for r in await conn.fetch(
            "SELECT id FROM interest_tags WHERE slug = ANY($1::text[])",
            list(brief.tag_slugs),
        )
    ]
    request_id = await conn.fetchval(
        """
        INSERT INTO trip_requests (
            session_token, mode, tag_ids, region_id, days, party_size, budget_band,
            origin_label, origin_lat, origin_lon, travel_month
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        RETURNING id
        """,
        session_token,
        brief.mode,
        tag_ids,
        region_id,
        brief.days,
        brief.party_size,
        brief.budget_band,
        brief.origin.label,
        brief.origin.lat,
        brief.origin.lon,
        brief.travel_month,
    )
    return UUID(str(request_id))


async def save_itinerary(conn: DbConn, itinerary: Itinerary) -> UUID:
    """Persist an itinerary and its POI audit trail.

    `itinerary_pois` is written alongside the jsonb payload so that "every
    rendered POI resolves to a real row" is checkable in SQL after the fact,
    not just asserted by the code that wrote it.
    """
    if itinerary.request_id is None:
        raise ValueError("itinerary must carry a request_id before it can be saved")

    async with conn.transaction():
        next_version = await conn.fetchval(
            "SELECT COALESCE(max(version), 0) + 1 FROM itineraries WHERE request_id = $1",
            itinerary.request_id,
        )
        payload = json.loads(itinerary.model_dump_json())

        itinerary_id = await conn.fetchval(
            """
            INSERT INTO itineraries (
                request_id, version, schema_version, payload, composer,
                llm_provider, llm_model, candidate_set_hash
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            RETURNING id
            """,
            itinerary.request_id,
            int(next_version),
            itinerary.schema_version,
            payload,
            itinerary.composer,
            itinerary.llm_provider,
            itinerary.llm_model,
            itinerary.candidate_set_hash,
        )
        resolved = UUID(str(itinerary_id))

        for poi_id, day_number, slot in itinerary.referenced_poi_ids():
            await conn.execute(
                """
                INSERT INTO itinerary_pois (itinerary_id, poi_id, day_number, slot)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (itinerary_id, day_number, slot) DO NOTHING
                """,
                resolved,
                poi_id,
                day_number,
                slot,
            )

    log.info(
        "store.itinerary_saved",
        itinerary_id=str(resolved),
        version=int(next_version),
        composer=itinerary.composer,
    )
    return resolved


async def latest_for_request(
    conn: DbConn, request_id: UUID
) -> tuple[UUID, dict[str, object]] | None:
    row = await conn.fetchrow(
        """
        SELECT id, payload FROM itineraries
        WHERE request_id = $1 ORDER BY version DESC LIMIT 1
        """,
        request_id,
    )
    if row is None:
        return None
    return UUID(str(row["id"])), dict(row["payload"])


async def load_brief(conn: DbConn, request_id: UUID) -> TripBrief | None:
    """Rebuild the brief from a persisted request.

    The worker runs in a different process from the API, so it reconstructs the
    brief from the database rather than receiving it. `trip_requests` is the
    immutable record of what was asked, which makes a job replayable: re-running
    it produces the same brief even if the taxonomy or seed data has since moved.
    """
    row = await conn.fetchrow(
        """
        SELECT tr.mode, tr.tag_ids, tr.days, tr.party_size, tr.budget_band,
               tr.origin_label, tr.origin_lat, tr.origin_lon, tr.created_at,
               tr.travel_month, r.slug AS district_slug
        FROM trip_requests tr
        LEFT JOIN regions r ON r.id = tr.region_id
        WHERE tr.id = $1
        """,
        request_id,
    )
    if row is None:
        return None

    tag_slugs = [
        str(r["slug"])
        for r in await conn.fetch(
            "SELECT slug FROM interest_tags WHERE id = ANY($1::smallint[]) ORDER BY display_order",
            list(row["tag_ids"] or []),
        )
    ]

    return TripBrief(
        request_id=request_id,
        mode=row["mode"],
        tag_slugs=tuple(tag_slugs),
        district_slug=str(row["district_slug"] or ""),
        days=int(row["days"]),
        party_size=int(row["party_size"]),
        budget_band=int(row["budget_band"]),
        origin=OriginRef(
            label=str(row["origin_label"]),
            lat=float(row["origin_lat"]),
            lon=float(row["origin_lon"]),
        ),
        # The month the user intends to travel, which drives the seasonal filter.
        # NULL only for requests predating migration 003, where creation month is
        # genuinely the best available guess.
        travel_month=int(row["travel_month"] or row["created_at"].month),
    )
