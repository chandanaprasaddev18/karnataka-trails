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

from typing import Any, Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel, Field

from tripplan.db import DbConn
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


async def district_path(conn: DbConn, slug: str) -> str | None:
    value = await conn.fetchval("SELECT path FROM regions WHERE slug = $1", slug)
    return str(value) if value is not None else None


async def fetch_candidates(
    conn: DbConn,
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
               r.slug AS region_slug, r.name AS region_name, r.media AS region_media,
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
                region=RegionRef(
                    slug=str(row["region_slug"]),
                    name=str(row["region_name"]),
                    media=list(row["region_media"] or []),
                ),
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


async def _guides_for(conn: DbConn, poi_ids: list[UUID]) -> dict[UUID, list[GuideRef]]:
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
    conn: DbConn,
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


async def interest_labels(conn: DbConn, slugs: tuple[str, ...]) -> list[dict[str, str]]:
    rows = await conn.fetch(
        """
        SELECT slug, label FROM interest_tags
        WHERE slug = ANY($1::text[]) AND kind = 'interest'
        ORDER BY display_order
        """,
        list(slugs),
    )
    return [{"slug": str(r["slug"]), "label": str(r["label"])} for r in rows]


async def region_ref(conn: DbConn, slug: str) -> RegionRef | None:
    row = await conn.fetchrow("SELECT slug, name, media FROM regions WHERE slug = $1", slug)
    if row is None:
        return None
    return RegionRef(
        slug=str(row["slug"]),
        name=str(row["name"]),
        media=list(row["media"] or []),
    )


# ---------------------------------------------------------------------------
# Feasibility — can this brief produce anything at all?
# ---------------------------------------------------------------------------
# Retrieval's filters are correct but unforgiving: a trekking trip in August
# legitimately matches nothing, because the trails are shut. Discovering that as
# a failed job is a bad experience and a worse diagnostic — the old message even
# blamed unpublished seed data, which sent the reader to the wrong place.
#
# This runs the same filters as `fetch_candidates`, then relaxes them one at a
# time to work out WHICH constraint is the blocker, and what would work instead.
# The answer is a cheap set of counts, so it happens at request time and the
# caller gets it immediately rather than after a round trip through the queue.


class Feasibility(BaseModel):
    """Whether a brief can be planned, and if not, what would work."""

    ok: bool
    reason: Literal["ok", "out_of_season", "budget_too_low", "nothing_tagged", "no_data"] = "ok"
    candidates: int = 0
    # The month that was asked for, so the explanation can name it and the client
    # can show it back. A plain field rather than a private attribute: this is
    # part of the payload the API returns.
    asked_month: int = 1
    # Months in which the REQUESTED interests do have candidates.
    suggested_months: list[int] = Field(default_factory=list)
    # Interests that do have candidates in the REQUESTED month.
    suggested_interests: list[dict[str, str]] = Field(default_factory=list)
    # The cheapest band that would unblock the brief, when budget is the blocker.
    min_budget_band: int | None = None

    def explain(self) -> str:
        """A message written for the person who asked, not for the logs."""
        if self.ok:
            return "ok"
        if self.reason == "out_of_season":
            months = ", ".join(_MONTH_NAMES[m] for m in self.suggested_months)
            return (
                "Nothing on that list is open in "
                f"{_MONTH_NAMES[self.asked_month]}. "
                + (f"These interests work best in {months}. " if months else "")
                + "Most treks and waterfalls in this district close or become unsafe "
                "during the monsoon."
            )
        if self.reason == "budget_too_low":
            return (
                "Everything that matches is above your budget. The cheapest option "
                f"for this month sits at band {self.min_budget_band} of 5."
            )
        if self.reason == "nothing_tagged":
            return "Nothing in our data for this district matches those interests yet."
        return (
            "No places have been published for this district yet. If you are running "
            "this locally, seed the data with `make seed && make publish`."
        )


_MONTH_NAMES = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}


async def feasibility(conn: DbConn, brief: TripBrief) -> Feasibility:
    """Diagnose whether a brief can be planned, relaxing one filter at a time."""
    path = await district_path(conn, brief.district_slug)
    if path is None:
        return Feasibility(ok=False, reason="no_data")

    # Counts over the district's published places/activities, sliced by which
    # constraint is applied. Comparing the slices identifies the blocker.
    row = await conn.fetchrow(
        """
        WITH scoped AS (
            SELECT p.id, p.cost_band, p.best_months,
                   EXISTS (
                       SELECT 1 FROM poi_tags pt
                       JOIN interest_tags it ON it.id = pt.tag_id
                       WHERE pt.poi_id = p.id AND it.slug = ANY($1::text[])
                   ) AS matches_interest
            FROM pois p
            JOIN regions r ON r.id = p.region_id
            WHERE p.status = 'published'
              AND p.kind IN ('place', 'activity')
              AND r.path LIKE $2 || '%'
        )
        SELECT
            count(*) AS published_in_district,
            count(*) FILTER (WHERE matches_interest) AS tagged,
            count(*) FILTER (
                WHERE matches_interest
                  AND (best_months IS NULL OR $3 = ANY(best_months))
            ) AS in_season,
            count(*) FILTER (
                WHERE matches_interest
                  AND (cost_band IS NULL OR cost_band <= $4)
            ) AS in_budget,
            count(*) FILTER (
                WHERE matches_interest
                  AND (best_months IS NULL OR $3 = ANY(best_months))
                  AND (cost_band IS NULL OR cost_band <= $4)
            ) AS exact,
            min(cost_band) FILTER (
                WHERE matches_interest
                  AND (best_months IS NULL OR $3 = ANY(best_months))
            ) AS cheapest_in_season
        FROM scoped
        """,
        list(brief.tag_slugs),
        path,
        brief.travel_month,
        brief.budget_band,
    )
    assert row is not None

    result = Feasibility(
        ok=int(row["exact"]) > 0,
        candidates=int(row["exact"]),
        asked_month=brief.travel_month,
    )
    if result.ok:
        return result

    # Attribute the failure to a single cause, cheapest fix first.
    if int(row["published_in_district"]) == 0:
        result.reason = "no_data"
        return result
    if int(row["tagged"]) == 0:
        result.reason = "nothing_tagged"
    elif int(row["in_season"]) == 0:
        result.reason = "out_of_season"
    elif int(row["in_budget"]) == 0 or int(row["exact"]) == 0:
        result.reason = "budget_too_low"
        result.min_budget_band = row["cheapest_in_season"]
    else:  # pragma: no cover — the slices above are exhaustive
        result.reason = "nothing_tagged"

    result.suggested_months = await _months_for_interests(conn, brief, path)
    result.suggested_interests = await _interests_for_month(conn, brief, path)
    return result


async def _months_for_interests(conn: DbConn, brief: TripBrief, path: str) -> list[int]:
    """Months in which the requested interests have something to offer."""
    rows = await conn.fetch(
        """
        SELECT m FROM generate_series(1, 12) AS m
        WHERE EXISTS (
            SELECT 1 FROM pois p
            JOIN regions r ON r.id = p.region_id
            JOIN poi_tags pt ON pt.poi_id = p.id
            JOIN interest_tags it ON it.id = pt.tag_id
            WHERE p.status = 'published'
              AND p.kind IN ('place', 'activity')
              AND r.path LIKE $2 || '%'
              AND it.slug = ANY($1::text[])
              AND (p.cost_band IS NULL OR p.cost_band <= $3)
              AND (p.best_months IS NULL OR m = ANY(p.best_months))
        )
        ORDER BY m
        """,
        list(brief.tag_slugs),
        path,
        brief.budget_band,
    )
    return [int(r["m"]) for r in rows]


async def _interests_for_month(conn: DbConn, brief: TripBrief, path: str) -> list[dict[str, str]]:
    """Interests that do have candidates in the month and budget asked for.

    Filtered to those with enough to fill a day, so we do not redirect someone
    onto an interest backed by a single POI.
    """
    rows = await conn.fetch(
        """
        SELECT it.slug, it.label, count(*) AS n
        FROM pois p
        JOIN regions r ON r.id = p.region_id
        JOIN poi_tags pt ON pt.poi_id = p.id
        JOIN interest_tags it ON it.id = pt.tag_id
        WHERE p.status = 'published'
          AND p.kind IN ('place', 'activity')
          AND r.path LIKE $1 || '%'
          AND it.kind = 'interest'
          AND it.is_active
          AND (p.cost_band IS NULL OR p.cost_band <= $2)
          AND (p.best_months IS NULL OR $3 = ANY(p.best_months))
        GROUP BY it.slug, it.label, it.display_order
        HAVING count(*) >= 3
        ORDER BY count(*) DESC, it.display_order
        LIMIT 6
        """,
        path,
        brief.budget_band,
        brief.travel_month,
    )
    return [{"slug": str(r["slug"]), "label": str(r["label"])} for r in rows]
