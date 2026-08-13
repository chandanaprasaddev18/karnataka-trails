"""Candidate retrieval — stage 1 of the engine.

This module is the ONLY place where a planning mode's filter lives. All three
modes run the same query shape and differ in one WHERE clause:

    by Interest  poi_tags matches the requested interests, within the district
    by District  regions.path LIKE '<district path>%', interests optional
    by Location  haversine_km(...) <= radius from the anchor, district ignored

See `_scope_for`, which is that one clause.

Three deliberate asymmetries:

1. **Places and activities must match an interest; stays must not.** Interest is
   the point of the plan for the things you *do*. A stay is logistics — you need
   somewhere to sleep near the day's cluster whether or not the property happens
   to be tagged 'trekking'. Requiring a tag match on stays produces itineraries
   with nowhere to sleep.
2. **Untagged rows are excluded from places/activities — in interest mode only.**
   A place nobody has tagged cannot be argued to match a requested interest, so it
   stays out rather than being padded in.
3. **Outside interest mode, interests rank rather than filter.** "Everything in
   this district, and I lean towards waterfalls" is a coherent request. Treating
   the lean as a filter would answer it with an empty trip, which is not.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel, Field

from tripplan.db import DbConn
from tripplan.domain.models import (
    AnchorRef,
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


class _Scope(BaseModel):
    """The mode-specific half of the retrieval query.

    Extracted so the three modes are visibly one query with one clause swapped,
    which is the claim `docs/architecture.md` makes. `sql` is built from module
    constants and fixed placeholder numbers — never from user input, which
    arrives as bound parameters in `params`.
    """

    sql: str
    params: list[Any]
    order: str


def _scope_for(brief: TripBrief, path: str, next_param: int) -> _Scope:
    """Build the WHERE fragment and ranking that distinguish the planning modes.

    Placeholders start at `next_param` so this composes with the fixed parameters
    the shared query already binds.
    """
    if brief.mode == "location" and brief.anchor is not None and brief.radius_km is not None:
        # Radius, not district. A location anchor near a district border should
        # legitimately return places on the other side of it — that is the whole
        # point of asking "what is near me" instead of "what is in this district".
        return _Scope(
            sql=(
                f"AND haversine_km(p.lat, p.lon, ${next_param}, ${next_param + 1}) "
                f"<= ${next_param + 2}"
            ),
            params=[brief.anchor.point.lat, brief.anchor.point.lon, float(brief.radius_km)],
            # Closest first, but a strongly matching interest still outranks mere
            # proximity: a landmark 40 km away beats a lay-by 2 km away.
            order=(
                "ORDER BY COALESCE(m.match_weight, 0) DESC, "
                f"haversine_km(p.lat, p.lon, ${next_param}, ${next_param + 1}) ASC, "
                "p.data_confidence DESC, p.name"
            ),
        )

    scope = _Scope(
        sql=f"AND r.path LIKE ${next_param} || '%'",
        params=[path],
        order="ORDER BY COALESCE(m.match_weight, 0) DESC, p.data_confidence DESC, p.name",
    )
    if brief.mode == "district":
        # District mode asks for the district's best, so confidence leads. Any
        # interests given are a tiebreaker, not a filter.
        scope = scope.model_copy(
            update={
                "order": (
                    "ORDER BY p.data_confidence DESC, COALESCE(m.match_weight, 0) DESC, p.name"
                )
            }
        )
    return scope


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
    # Stays are logistics, not interests — see the module docstring. And outside
    # interest mode, NOTHING requires a tag match: "everything in this district"
    # and "everything near me" are requests about scope, not about taste, so an
    # interest given in those modes ranks results instead of excluding them.
    require_tags = kind != "stay" and brief.mode == "interest"
    tag_join = "JOIN" if require_tags else "LEFT JOIN"
    # The shared query binds $1..$5; the mode's clause continues from $6.
    scope = _scope_for(brief, path, next_param=6)

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
          AND (p.cost_band IS NULL OR p.cost_band <= $3)
          AND (p.best_months IS NULL OR $4 = ANY(p.best_months))
          {scope.sql}
        {scope.order}
        LIMIT $5
        """,  # noqa: S608 — interpolations are module constants keyed by a Literal
        list(brief.tag_slugs),
        kind,
        brief.budget_band,
        brief.travel_month,
        limit,
        *scope.params,
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


# ---------------------------------------------------------------------------
# Anchors — what location mode plans around
# ---------------------------------------------------------------------------
# An anchor is a published POI or a region we hold, never free coordinates. The
# picker offers both because "near Mullayanagiri" and "near Mudigere" are equally
# reasonable ways to say where you are, and only one of them is a POI.


async def search_anchors(conn: DbConn, query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Anchors matching a typed fragment, best first.

    Ranked by how much is actually near them, not by string similarity: a user
    typing "chik" wants the town they can plan a trip around, not whichever row
    happens to match earliest. Regions carry a taluk's whole catchment, so they
    tend to lead — which is the right answer for "where are you staying?".
    """
    like = f"%{query.strip().lower()}%"
    rows = await conn.fetch(
        """
        WITH anchors AS (
            -- Published POIs. Draft rows are excluded for the same reason they
            -- never reach an itinerary: we have not checked them.
            SELECT 'poi' AS kind, p.slug, p.name AS label,
                   r.name || ' · ' || COALESCE(pd.place_type, p.kind) AS sublabel,
                   p.lat, p.lon
            FROM pois p
            JOIN regions r ON r.id = p.region_id
            LEFT JOIN place_details pd ON pd.poi_id = p.id
            WHERE p.status = 'published' AND p.kind = 'place'
            UNION ALL
            -- Regions with coordinates. A district or taluk without a centroid
            -- cannot anchor a radius search, so it is not offered.
            SELECT 'region' AS kind, r.slug, r.name AS label,
                   initcap(r.kind) AS sublabel,
                   r.centroid_lat AS lat, r.centroid_lon AS lon
            FROM regions r
            WHERE r.centroid_lat IS NOT NULL
              AND r.centroid_lon IS NOT NULL
              AND r.kind IN ('district', 'taluk', 'locality')
        )
        SELECT a.*, (
            SELECT count(*) FROM pois n
            WHERE n.status = 'published'
              AND n.kind IN ('place', 'activity')
              AND haversine_km(n.lat, n.lon, a.lat, a.lon) <= 60
        ) AS nearby
        FROM anchors a
        WHERE lower(a.label) LIKE $1 OR a.slug LIKE $1
        ORDER BY nearby DESC, length(a.label), a.label
        LIMIT $2
        """,
        like,
        limit,
    )
    return [dict(r) for r in rows]


async def resolve_anchor(conn: DbConn, slug: str) -> tuple[AnchorRef, str] | None:
    """An anchor slug to (anchor, district slug), or None if we do not hold it.

    The district comes back too because every itinerary still names one — location
    mode derives it from where the anchor sits rather than asking again.
    """
    row = await conn.fetchrow(
        """
        SELECT 'poi' AS kind, p.slug, p.name AS label, p.lat, p.lon, r.path
        FROM pois p JOIN regions r ON r.id = p.region_id
        WHERE p.slug = $1 AND p.status = 'published'
        UNION ALL
        SELECT 'region' AS kind, r.slug, r.name AS label,
               r.centroid_lat AS lat, r.centroid_lon AS lon, r.path
        FROM regions r
        WHERE r.slug = $1 AND r.centroid_lat IS NOT NULL
        LIMIT 1
        """,
        slug,
    )
    if row is None:
        return None

    # The district is the second path segment: /karnataka/chikkamagaluru/mudigere/
    # -> chikkamagaluru. Derived from the materialised ancestry rather than a
    # second query, which is what `path` is for.
    segments = [s for s in str(row["path"]).split("/") if s]
    district_slug = segments[1] if len(segments) > 1 else segments[0]

    anchor = AnchorRef(
        kind="poi" if row["kind"] == "poi" else "region",
        slug=str(row["slug"]),
        label=str(row["label"]),
        point=GeoPoint(lat=float(row["lat"]), lon=float(row["lon"])),
    )
    return anchor, district_slug


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
    reason: Literal[
        "ok",
        "out_of_season",
        "budget_too_low",
        "nothing_tagged",
        "nothing_in_radius",
        "no_data",
    ] = "ok"
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
    # Location mode: a wider radius that would find something, when the chosen one
    # found nothing.
    suggested_radius_km: int | None = None

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
        if self.reason == "nothing_in_radius":
            return (
                "Nothing we have published falls within that radius. "
                + (
                    f"Widening it to {self.suggested_radius_km} km would find something."
                    if self.suggested_radius_km
                    else "Try a different starting point."
                )
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

    # Counts over the published places/activities IN SCOPE, sliced by which
    # constraint is applied. Comparing the slices identifies the blocker. Scope is
    # the same mode-specific clause retrieval uses, from the same function, so the
    # two can never disagree about what was considered.
    scope = _scope_for(brief, path, next_param=5)
    row = await conn.fetchrow(
        f"""
        WITH scoped AS (
            SELECT p.id, p.cost_band, p.best_months,
                   -- Outside interest mode nothing is excluded for lacking a tag,
                   -- so the diagnosis must use the same rule retrieval does or
                   -- the two would disagree about why a brief failed.
                   ($4 OR EXISTS (
                       SELECT 1 FROM poi_tags pt
                       JOIN interest_tags it ON it.id = pt.tag_id
                       WHERE pt.poi_id = p.id AND it.slug = ANY($1::text[])
                   )) AS matches_interest
            FROM pois p
            JOIN regions r ON r.id = p.region_id
            WHERE p.status = 'published'
              AND p.kind IN ('place', 'activity')
              {scope.sql}
        )
        SELECT
            count(*) AS published_in_district,
            count(*) FILTER (WHERE matches_interest) AS tagged,
            count(*) FILTER (
                WHERE matches_interest
                  AND (best_months IS NULL OR $2 = ANY(best_months))
            ) AS in_season,
            count(*) FILTER (
                WHERE matches_interest
                  AND (cost_band IS NULL OR cost_band <= $3)
            ) AS in_budget,
            count(*) FILTER (
                WHERE matches_interest
                  AND (best_months IS NULL OR $2 = ANY(best_months))
                  AND (cost_band IS NULL OR cost_band <= $3)
            ) AS exact,
            min(cost_band) FILTER (
                WHERE matches_interest
                  AND (best_months IS NULL OR $2 = ANY(best_months))
            ) AS cheapest_in_season
        FROM scoped
        """,  # noqa: S608 — the scope clause is a module constant; values are bound
        list(brief.tag_slugs),
        brief.travel_month,
        brief.budget_band,
        brief.mode != "interest",
        *scope.params,
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
        # An empty district means unseeded data; an empty RADIUS means the user
        # drew too small a circle, which is a different message and a different
        # fix. Saying "seed the database" to someone who picked a quiet village
        # sends them somewhere they cannot act.
        if brief.mode == "location":
            result.reason = "nothing_in_radius"
            result.suggested_radius_km = await _radius_that_would_work(conn, brief)
        else:
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
    """Months in which the brief's subject has something to offer.

    "Subject" is the requested interests when there are any, and everything in
    scope when there are not — district mode with no interests can still be out of
    season, and answering "try January" needs the same question asked without a
    tag filter.
    """
    scope = _scope_for(brief, path, next_param=3)
    tag_filter = (
        """AND EXISTS (
                  SELECT 1 FROM poi_tags pt
                  JOIN interest_tags it ON it.id = pt.tag_id
                  WHERE pt.poi_id = p.id AND it.slug = ANY($1::text[])
              )"""
        if brief.tag_slugs
        else ""
    )
    rows = await conn.fetch(
        f"""
        SELECT m FROM generate_series(1, 12) AS m
        WHERE EXISTS (
            SELECT 1 FROM pois p
            JOIN regions r ON r.id = p.region_id
            WHERE p.status = 'published'
              AND p.kind IN ('place', 'activity')
              AND (p.cost_band IS NULL OR p.cost_band <= $2)
              AND (p.best_months IS NULL OR m = ANY(p.best_months))
              {tag_filter}
              {scope.sql}
        )
        ORDER BY m
        """,  # noqa: S608 — fragments are module constants; values are bound
        list(brief.tag_slugs),
        brief.budget_band,
        *scope.params,
    )
    return [int(r["m"]) for r in rows]


async def _interests_for_month(conn: DbConn, brief: TripBrief, path: str) -> list[dict[str, str]]:
    """Interests that do have candidates in the month and budget asked for.

    Filtered to those with enough to fill a day, so we do not redirect someone
    onto an interest backed by a single POI.
    """
    scope = _scope_for(brief, path, next_param=3)
    rows = await conn.fetch(
        f"""
        SELECT it.slug, it.label, count(*) AS n
        FROM pois p
        JOIN regions r ON r.id = p.region_id
        JOIN poi_tags pt ON pt.poi_id = p.id
        JOIN interest_tags it ON it.id = pt.tag_id
        WHERE p.status = 'published'
          AND p.kind IN ('place', 'activity')
          AND it.kind = 'interest'
          AND it.is_active
          AND (p.cost_band IS NULL OR p.cost_band <= $1)
          AND (p.best_months IS NULL OR $2 = ANY(p.best_months))
          {scope.sql}
        GROUP BY it.slug, it.label, it.display_order
        HAVING count(*) >= 3
        ORDER BY count(*) DESC, it.display_order
        LIMIT 6
        """,  # noqa: S608 — fragments are module constants; values are bound
        brief.budget_band,
        brief.travel_month,
        *scope.params,
    )
    return [{"slug": str(r["slug"]), "label": str(r["label"])} for r in rows]


async def _radius_that_would_work(conn: DbConn, brief: TripBrief) -> int | None:
    """The smallest sensible radius with enough nearby to plan, or None.

    Offered instead of a bare "nothing found": the fix for an empty 25 km search
    is almost always a wider one, and the user should not have to guess how much
    wider. Rounded to the increments the slider actually offers.
    """
    if brief.anchor is None:
        return None
    for candidate_radius in (25, 50, 75, 100, 150, 200):
        if brief.radius_km is not None and candidate_radius <= brief.radius_km:
            continue
        found = await conn.fetchval(
            """
            SELECT count(*) FROM pois p
            WHERE p.status = 'published'
              AND p.kind IN ('place', 'activity')
              AND (p.cost_band IS NULL OR p.cost_band <= $1)
              AND (p.best_months IS NULL OR $2 = ANY(p.best_months))
              AND haversine_km(p.lat, p.lon, $3, $4) <= $5
            """,
            brief.budget_band,
            brief.travel_month,
            brief.anchor.point.lat,
            brief.anchor.point.lon,
            float(candidate_radius),
        )
        if int(found or 0) >= 3:
            return candidate_radius
    return None
