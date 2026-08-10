"""Persistence for travel estimates.

`travel_estimates` has `source` in its primary key, so Phase 3's real ETAs land
alongside Phase 1's placeholders instead of overwriting them. Reads therefore
pick the best available source per pair using an explicit preference order,
which means the maps rollout can be partial — a pair with a real ETA uses it,
a pair without falls back — with no data migration and no per-pair config.
"""

from __future__ import annotations

from uuid import UUID

from tripplan.db import DbConn
from tripplan.domain.models import TravelLeg
from tripplan.domain.taxonomy import TRAVEL_SOURCE_PREFERENCE, TravelSource

PoiPair = tuple[UUID, UUID]


async def load_legs(conn: DbConn, poi_ids: list[UUID]) -> dict[PoiPair, TravelLeg]:
    """Best cached leg for every pair among `poi_ids`, honouring the source preference.

    Fetches by endpoint membership rather than by explicit pair list: a candidate
    set is a few dozen POIs, so one small query beats composite-array binding.
    """
    if not poi_ids:
        return {}

    rows = await conn.fetch(
        """
        SELECT from_poi_id, to_poi_id, source, distance_km, duration_minutes
        FROM travel_estimates
        WHERE from_poi_id = ANY($1::uuid[]) AND to_poi_id = ANY($1::uuid[])
        """,
        poi_ids,
    )

    ranked: dict[PoiPair, tuple[int, TravelLeg]] = {}
    for row in rows:
        source: TravelSource = row["source"]
        try:
            rank = TRAVEL_SOURCE_PREFERENCE.index(source)
        except ValueError:  # unknown source; treat as worst
            rank = len(TRAVEL_SOURCE_PREFERENCE)
        key = (UUID(str(row["from_poi_id"])), UUID(str(row["to_poi_id"])))
        leg = TravelLeg(
            distance_km=float(row["distance_km"]),
            duration_minutes=int(row["duration_minutes"]),
            source=source,
        )
        current = ranked.get(key)
        if current is None or rank < current[0]:
            ranked[key] = (rank, leg)

    return {key: leg for key, (_, leg) in ranked.items()}


async def save_leg(conn: DbConn, from_poi: UUID, to_poi: UUID, leg: TravelLeg) -> None:
    """Memoise a computed leg. A self-pair is skipped (the CHECK forbids it)."""
    if from_poi == to_poi:
        return
    await conn.execute(
        """
        INSERT INTO travel_estimates
            (from_poi_id, to_poi_id, source, distance_km, duration_minutes)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (from_poi_id, to_poi_id, source) DO UPDATE SET
            distance_km = EXCLUDED.distance_km,
            duration_minutes = EXCLUDED.duration_minutes,
            computed_at = now()
        """,
        from_poi,
        to_poi,
        leg.source,
        leg.distance_km,
        leg.duration_minutes,
    )
