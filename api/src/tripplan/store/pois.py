"""Candidate retrieval — stage 1 of the engine.

This module is the ONLY place where a planning mode's filter lives. All three
modes run the same query shape and differ in one WHERE clause:

    by Interest (Phase 1)  poi_tags.tag_id matches the requested interests
    by District (Phase 2)  regions.path LIKE '<district path>%'
    by Location (Phase 2)  haversine_km(...) < radius, anchored on a POI

Two deliberate asymmetries:

1. **Places and activities must match an interest; stays must not.** Interest is
   the point of the plan for the things you *do*. A stay is logistics — you need
   somewhere to sleep near the day's cluster whether or not the property happens
   to be tagged 'trekking'. Requiring a tag match on stays produces itineraries
   with nowhere to sleep.
2. **Untagged rows are excluded from places/activities.** A place nobody has
   tagged cannot be argued to match a requested interest, so it stays out rather
   than being padded in.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from tripplan.domain.models import (
    Candidate,
    CandidateSet,
    GeoPoint,
    GuideRef,
    Money,
    RegionRef,
    TripBrief,
)
from tripplan.domain.taxonomy import PoiKind
from tripplan.observability.logging import get_logger

log = get_logger(__name__)

# Detail columns to select per kind, and the table to join.
_DETAIL_SQL: dict[PoiKind, tuple[str, str]] = {
    "place": (
        "place_details",
        """
        pd.place_type, pd.best_time_of_day, pd.opening_hours,
        pd.entry_fee_paise, pd.requires_permit, pd.notes
        """,
    ),
    "stay": (
        "stay_details",
        """
        sd.stay_type, sd.per_night_min_paise, sd.per_night_max_paise,
        sd.max_occupancy, sd.meals_included, sd.amenities, sd.contact
        """,
    ),
    "activity": (
        "activity_details",
        """
        ad.activity_type, ad.physical_intensity, ad.min_age,
        ad.requires_guide, ad.requires_booking, ad.operator_name, ad.contact
        """,
    ),
}

_ALIAS: dict[PoiKind, str] = {"place": "pd", "stay": "sd", "activity": "ad"}

_REF_PREFIX: dict[PoiKind, str] = {"place": "P", "stay": "S", "activity": "A"}


async def district_path(conn: asyncpg.Connection, slug: str) -> str | None:
    value = await conn.fetchval("SELECT path FROM regions WHERE slug = $1", slug)
    return str(value) if value is not None else None


async def fetch_candidates(
    conn: asyncpg.Connection,
    brief: TripBrief,
    kind: PoiKind,
    limit: int,
) -> list[Candidate]:
    """Retrieve capped, ranked candidates of one kind for a brief."""
    path = await district_path(conn, brief.district_slug)
    if path is None:
        return []

    table, detail_cols = _DETAIL_SQL[kind]
    alias = _ALIAS[kind]
    # Stays are logistics, not interests — see the module docstring.
    require_tags = kind != "stay"
    tag_join = "JOIN" if require_tags else "LEFT JOIN"

    rows = await conn.fetch(
        f"""
        WITH matched AS (
            SELECT pt.poi_id,
                   max(pt.weight) AS match_weight
            FROM poi_tags pt
            JOIN interest_tags it ON it.id = pt.tag_id
            WHERE it.slug = ANY($1::text[])
            GROUP BY pt.poi_id
        ),
        all_tags AS (
            SELECT pt.poi_id, array_agg(it.slug ORDER BY it.slug) AS tags
            FROM poi_tags pt
            JOIN interest_tags it ON it.id = pt.tag_id
            GROUP BY pt.poi_id
        )
        SELECT p.id, p.kind, p.name, p.summary, p.lat, p.lon,
               p.typical_duration_minutes, p.cost_band,
               p.cost_min_paise, p.cost_max_paise, p.difficulty,
               p.is_repeatable, p.media, p.data_confidence,
               (p.verified_at IS NOT NULL) AS is_verified,
               r.slug AS region_slug, r.name AS region_name,
               COALESCE(m.match_weight, 0) AS match_weight,
               COALESCE(t.tags, '{{}}') AS tags,
               {detail_cols}
        FROM pois p
        JOIN regions r ON r.id = p.region_id
        {tag_join} matched m ON m.poi_id = p.id
        LEFT JOIN all_tags t ON t.poi_id = p.id
        LEFT JOIN {table} {alias} ON {alias}.poi_id = p.id
        WHERE p.status = 'published'
          AND p.kind = $2
          AND r.path LIKE $3 || '%'
          AND (p.cost_band IS NULL OR p.cost_band <= $4)
          AND (p.best_months IS NULL OR $5 = ANY(p.best_months))
        ORDER BY COALESCE(m.match_weight, 0) DESC,
                 p.data_confidence DESC,
                 p.name
        LIMIT $6
        """,  # noqa: S608 — interpolations are module constants keyed by a Literal
        list(brief.tag_slugs),
        kind,
        path,
        brief.budget_band,
        brief.travel_month,
        limit,
    )

    guides = await _guides_for(conn, [UUID(str(r["id"])) for r in rows])

    out: list[Candidate] = []
    for index, row in enumerate(rows, start=1):
        poi_id = UUID(str(row["id"]))
        out.append(
            Candidate(
                ref=f"{_REF_PREFIX[kind]}{index}",
                poi_id=poi_id,
                kind=kind,
                name=str(row["name"]),
                summary=str(row["summary"]),
                region=RegionRef(slug=str(row["region_slug"]), name=str(row["region_name"])),
                point=GeoPoint(lat=float(row["lat"]), lon=float(row["lon"])),
                duration_minutes=row["typical_duration_minutes"],
                cost_band=row["cost_band"],
                cost=_money(row["cost_min_paise"], row["cost_max_paise"]),
                difficulty=row["difficulty"],
                is_repeatable=bool(row["is_repeatable"]),
                tags=tuple(row["tags"] or ()),
                match_weight=int(row["match_weight"]),
                data_confidence=int(row["data_confidence"]),
                is_verified=bool(row["is_verified"]),
                detail=_detail_dict(kind, row),
                media=list(row["media"] or []),
                guides=guides.get(poi_id, []),
            )
        )
    return out


def _money(min_paise: int | None, max_paise: int | None) -> Money | None:
    if min_paise is None and max_paise is None:
        return None
    low = min_paise if min_paise is not None else max_paise
    high = max_paise if max_paise is not None else min_paise
    return Money(min_paise=int(low or 0), max_paise=int(high or 0))


def _detail_dict(kind: PoiKind, row: asyncpg.Record) -> dict[str, Any]:
    """Kind-specific columns, minus the SQL nulls, as a plain dict."""
    keys: tuple[str, ...]
    if kind == "place":
        keys = (
            "place_type",
            "best_time_of_day",
            "opening_hours",
            "entry_fee_paise",
            "requires_permit",
            "notes",
        )
    elif kind == "stay":
        keys = (
            "stay_type",
            "per_night_min_paise",
            "per_night_max_paise",
            "max_occupancy",
            "meals_included",
            "amenities",
            "contact",
        )
    else:
        keys = (
            "activity_type",
            "physical_intensity",
            "min_age",
            "requires_guide",
            "requires_booking",
            "operator_name",
            "contact",
        )
    return {k: row[k] for k in keys if row[k] is not None}


async def _guides_for(conn: asyncpg.Connection, poi_ids: list[UUID]) -> dict[UUID, list[GuideRef]]:
    """Published guides attached to these POIs, keyed by POI.

    Only published guides: an unverified placeholder must never be presented to
    a traveller as someone to call.
    """
    if not poi_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT pg.poi_id, g.id, g.name, g.languages, g.contact, g.is_verified
        FROM poi_guides pg
        JOIN guides g ON g.id = pg.guide_id
        WHERE pg.poi_id = ANY($1::uuid[]) AND g.status = 'published'
        ORDER BY g.is_verified DESC, g.name
        """,
        poi_ids,
    )
    out: dict[UUID, list[GuideRef]] = {}
    for row in rows:
        out.setdefault(UUID(str(row["poi_id"])), []).append(
            GuideRef(
                guide_id=UUID(str(row["id"])),
                name=str(row["name"]),
                languages=list(row["languages"] or []),
                contact=dict(row["contact"] or {}),
                is_verified=bool(row["is_verified"]),
            )
        )
    return out


async def retrieve(
    conn: asyncpg.Connection,
    brief: TripBrief,
    *,
    max_places: int,
    max_stays: int,
    max_activities: int,
) -> CandidateSet:
    """Build the full candidate set for a brief."""
    candidates = CandidateSet(
        places=await fetch_candidates(conn, brief, "place", max_places),
        stays=await fetch_candidates(conn, brief, "stay", max_stays),
        activities=await fetch_candidates(conn, brief, "activity", max_activities),
    )
    log.info(
        "engine.retrieval",
        district=brief.district_slug,
        interests=list(brief.tag_slugs),
        places=len(candidates.places),
        stays=len(candidates.stays),
        activities=len(candidates.activities),
        fingerprint=candidates.fingerprint()[:12],
    )
    return candidates


async def interest_labels(conn: asyncpg.Connection, slugs: tuple[str, ...]) -> list[dict[str, str]]:
    rows = await conn.fetch(
        """
        SELECT slug, label FROM interest_tags
        WHERE slug = ANY($1::text[]) AND kind = 'interest'
        ORDER BY display_order
        """,
        list(slugs),
    )
    return [{"slug": str(r["slug"]), "label": str(r["label"])} for r in rows]


async def region_ref(conn: asyncpg.Connection, slug: str) -> RegionRef | None:
    row = await conn.fetchrow("SELECT slug, name FROM regions WHERE slug = $1", slug)
    if row is None:
        return None
    return RegionRef(slug=str(row["slug"]), name=str(row["name"]))
