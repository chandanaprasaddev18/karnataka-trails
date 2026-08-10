"""Seed loaders for the curated dataset.

Every loader is **idempotent** — it upserts on the natural key (`slug`), so
`make seed` can run repeatedly without duplicating rows or resetting the
database. That matters because the seed files are edited by hand during
fact-checking and reloaded often.

The YAML is validated through Pydantic models with ``extra="forbid"``, so a
misspelled key is a startup error naming the field rather than a silently
ignored value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg
import yaml
from pydantic import BaseModel, ConfigDict, Field

from tripplan.domain.taxonomy import (
    ActivityType,
    PlaceType,
    PoiKind,
    RegionKind,
    StayType,
    TagKind,
    TimeOfDay,
)
from tripplan.observability.logging import get_logger

log = get_logger(__name__)

# Rows carrying this source are synthetic scaffolding, not curated data. The
# publish gate refuses to promote them unless explicitly overridden, so a
# placeholder can never reach a traveller by accident.
PLACEHOLDER_SOURCE = "placeholder"


class SeedError(RuntimeError):
    """Raised when a seed file is structurally invalid."""


# ---------------------------------------------------------------------------
# Seed file schemas
# ---------------------------------------------------------------------------


class TagSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    label: str
    kind: TagKind
    display_order: int = 100
    description: str | None = None


class RegionSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    kind: RegionKind
    # [lat, lon]; optional because a locality may not have one yet.
    centroid: tuple[float, float] | None = None
    children: list[RegionSeed] = Field(default_factory=list)


def _read_yaml_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SeedError(f"seed file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SeedError(f"{path.name}: expected a top-level list, got {type(raw).__name__}")
    return raw


# ---------------------------------------------------------------------------
# interest_tags
# ---------------------------------------------------------------------------


async def load_interest_tags(conn: asyncpg.Connection, seeds_dir: Path) -> int:
    """Upsert the tag vocabulary. Returns the number of rows written."""
    path = seeds_dir / "interest_tags.yaml"
    tags = [TagSeed.model_validate(row) for row in _read_yaml_list(path)]

    slugs = [t.slug for t in tags]
    duplicates = {s for s in slugs if slugs.count(s) > 1}
    if duplicates:
        raise SeedError(f"{path.name}: duplicate slugs {sorted(duplicates)}")

    async with conn.transaction():
        for tag in tags:
            await conn.execute(
                """
                INSERT INTO interest_tags (slug, label, kind, description, display_order)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (slug) DO UPDATE SET
                    label         = EXCLUDED.label,
                    kind          = EXCLUDED.kind,
                    description   = EXCLUDED.description,
                    display_order = EXCLUDED.display_order,
                    is_active     = true
                """,
                tag.slug,
                tag.label,
                tag.kind,
                tag.description,
                tag.display_order,
            )

    log.info("seed.interest_tags", count=len(tags))
    return len(tags)


# ---------------------------------------------------------------------------
# regions
# ---------------------------------------------------------------------------


async def load_regions(conn: asyncpg.Connection, seeds_dir: Path) -> int:
    """Upsert the region hierarchy, deriving `path` from the YAML nesting.

    Deriving rather than declaring the path is deliberate: the nesting in the
    file is the single source of truth, so the column cannot disagree with the
    tree the way a hand-written path would eventually.
    """
    path_file = seeds_dir / "regions.yaml"
    roots = [RegionSeed.model_validate(row) for row in _read_yaml_list(path_file)]

    seen: set[str] = set()
    written = 0

    async def upsert(node: RegionSeed, parent_id: int | None, parent_path: str) -> None:
        nonlocal written
        if node.slug in seen:
            raise SeedError(
                f"{path_file.name}: duplicate slug {node.slug!r}. "
                "Region slugs are globally unique — a taluk sharing its district's "
                "name needs a distinct slug (e.g. 'chikkamagaluru-taluk')."
            )
        seen.add(node.slug)

        node_path = f"{parent_path}{node.slug}/"
        lat, lon = node.centroid if node.centroid else (None, None)

        region_id = await conn.fetchval(
            """
            INSERT INTO regions (parent_id, kind, name, slug, path, centroid_lat, centroid_lon)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (slug) DO UPDATE SET
                parent_id    = EXCLUDED.parent_id,
                kind         = EXCLUDED.kind,
                name         = EXCLUDED.name,
                path         = EXCLUDED.path,
                centroid_lat = EXCLUDED.centroid_lat,
                centroid_lon = EXCLUDED.centroid_lon
            RETURNING id
            """,
            parent_id,
            node.kind,
            node.name,
            node.slug,
            node_path,
            lat,
            lon,
        )
        written += 1

        for child in node.children:
            await upsert(child, int(region_id), node_path)

    async with conn.transaction():
        for root in roots:
            await upsert(root, None, "/")

    log.info("seed.regions", count=written)
    return written


# ---------------------------------------------------------------------------
# POI seed schemas
# ---------------------------------------------------------------------------
# One model per kind detail block. Which block is present determines nothing —
# the loader is told the expected kind by the caller and checks that the
# matching block is the one supplied, so a stay accidentally filed in
# places.yaml is an error rather than a silently mis-typed row.


class PlaceDetailSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: PlaceType
    best_time_of_day: TimeOfDay | None = None
    opening_hours: dict[str, Any] | None = None
    entry_fee_paise: int | None = Field(default=None, ge=0)
    requires_permit: bool = False
    notes: str | None = None


class StayDetailSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: StayType
    per_night: tuple[int, int] | None = None
    max_occupancy: int | None = Field(default=None, gt=0)
    meals_included: bool = False
    amenities: list[str] = Field(default_factory=list)
    contact: dict[str, Any] = Field(default_factory=dict)


class ActivityDetailSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActivityType
    physical_intensity: int | None = Field(default=None, ge=1, le=5)
    min_age: int | None = Field(default=None, ge=0)
    requires_guide: bool = False
    requires_booking: bool = False
    operator_name: str | None = None
    contact: dict[str, Any] = Field(default_factory=dict)


class PoiSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    region: str  # region slug
    coords: tuple[float, float]  # [lat, lon]
    summary: str
    description: str | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    cost_band: int | None = Field(default=None, ge=1, le=5)
    cost: tuple[int, int] | None = None  # [min_paise, max_paise]
    difficulty: int | None = Field(default=None, ge=1, le=5)
    best_months: list[int] | None = None
    is_repeatable: bool = False
    # tag slug -> weight (1-100). The weight is what lets stage 1 rank a real
    # peak above a gentle garden walk for the same 'trekking' tag.
    tags: dict[str, int] = Field(default_factory=dict)
    source: str
    source_url: str | None = None
    confidence: int = Field(default=3, ge=1, le=5)

    place: PlaceDetailSeed | None = None
    stay: StayDetailSeed | None = None
    activity: ActivityDetailSeed | None = None

    def detail_for(self, kind: PoiKind) -> PlaceDetailSeed | StayDetailSeed | ActivityDetailSeed:
        # Report a MISFILED block before a missing one: "you put a stay in
        # places.yaml" is the actual mistake, whereas "expected a place block"
        # just describes the symptom.
        others = [k for k in ("place", "stay", "activity") if k != kind and getattr(self, k)]
        if others:
            raise SeedError(
                f"{self.slug}: has a {others[0]!r} block but is filed as a {kind} — "
                f"move it to {others[0]}s.yaml"
            )
        block = {"place": self.place, "stay": self.stay, "activity": self.activity}[kind]
        if block is None:
            raise SeedError(f"{self.slug}: expected a '{kind}:' detail block")
        return block


class GuideSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    region: str
    languages: list[str] = Field(default_factory=list)
    day_rate_paise: int | None = Field(default=None, ge=0)
    is_verified: bool = False
    source: str
    confidence: int = Field(default=3, ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    pois: list[str] = Field(default_factory=list)  # POI slugs this guide covers


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


async def _tag_ids(conn: asyncpg.Connection) -> dict[str, int]:
    rows = await conn.fetch("SELECT slug, id FROM interest_tags")
    return {str(r["slug"]): int(r["id"]) for r in rows}


async def _region_ids(conn: asyncpg.Connection) -> dict[str, int]:
    rows = await conn.fetch("SELECT slug, id FROM regions")
    return {str(r["slug"]): int(r["id"]) for r in rows}


# ---------------------------------------------------------------------------
# POIs
# ---------------------------------------------------------------------------

_POI_FILES: tuple[tuple[PoiKind, str], ...] = (
    ("place", "places.yaml"),
    ("stay", "stays.yaml"),
    ("activity", "activities.yaml"),
)


class SeedReport(BaseModel):
    """What was loaded, and what a reviewer still needs to look at."""

    loaded: int = 0
    by_kind: dict[str, int] = Field(default_factory=dict)
    placeholders: list[str] = Field(default_factory=list)
    low_confidence: list[str] = Field(default_factory=list)
    untagged: list[str] = Field(default_factory=list)
    missing_duration: list[str] = Field(default_factory=list)
    missing_cost_band: list[str] = Field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"loaded {self.loaded} POI(s): "
            + ", ".join(f"{k}={v}" for k, v in sorted(self.by_kind.items()))
        ]

        def block(title: str, items: list[str]) -> None:
            if items:
                lines.append(f"  {title} ({len(items)}): {', '.join(sorted(items))}")

        block("synthetic placeholders — never publish these", self.placeholders)
        block("confidence <= 2 — verify before publishing", self.low_confidence)
        block("no tags — invisible to interest filtering", self.untagged)
        block("no duration — the day budget cannot account for these", self.missing_duration)
        block("no cost_band — invisible to budget filtering", self.missing_cost_band)
        return "\n".join(lines)


async def load_pois(conn: asyncpg.Connection, seeds_dir: Path, district: str) -> SeedReport:
    """Load places, stays and activities for one district.

    Rows land as ``status='draft'``. Promotion is a separate, deliberate act
    (`tripplan publish`), because the seed set is hand-compiled and retrieval
    must never see an unreviewed claim.

    Re-seeding preserves an existing row's ``status``: a reviewer who published
    a row should not have it silently demoted by a routine reload. The tradeoff
    is that editing a fact in a published row does not force re-verification —
    see the follow-up note in CLAUDE.md.
    """
    district_dir = seeds_dir / district
    if not district_dir.is_dir():
        raise SeedError(f"no seed directory for district {district!r}: {district_dir}")

    tags = await _tag_ids(conn)
    regions = await _region_ids(conn)
    report = SeedReport()

    async with conn.transaction():
        for kind, filename in _POI_FILES:
            rows = [PoiSeed.model_validate(r) for r in _read_yaml_list(district_dir / filename)]
            report.by_kind[kind] = len(rows)

            for poi in rows:
                detail = poi.detail_for(kind)

                if poi.region not in regions:
                    raise SeedError(
                        f"{poi.slug}: unknown region {poi.region!r} — add it to regions.yaml"
                    )
                unknown = sorted(set(poi.tags) - set(tags))
                if unknown:
                    raise SeedError(
                        f"{poi.slug}: unknown tag slug(s) {unknown} — "
                        "add them to interest_tags.yaml or fix the spelling"
                    )
                bad_weights = {s: w for s, w in poi.tags.items() if not 1 <= w <= 100}
                if bad_weights:
                    raise SeedError(f"{poi.slug}: tag weights must be 1-100, got {bad_weights}")

                lat, lon = poi.coords
                cost_min, cost_max = poi.cost if poi.cost else (None, None)

                poi_id = await conn.fetchval(
                    """
                    INSERT INTO pois (
                        kind, name, slug, region_id, lat, lon, summary, description,
                        typical_duration_minutes, cost_band, cost_min_paise, cost_max_paise,
                        difficulty, best_months, is_repeatable,
                        source, source_url, data_confidence
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
                    ON CONFLICT (slug) DO UPDATE SET
                        kind = EXCLUDED.kind,
                        name = EXCLUDED.name,
                        region_id = EXCLUDED.region_id,
                        lat = EXCLUDED.lat,
                        lon = EXCLUDED.lon,
                        summary = EXCLUDED.summary,
                        description = EXCLUDED.description,
                        typical_duration_minutes = EXCLUDED.typical_duration_minutes,
                        cost_band = EXCLUDED.cost_band,
                        cost_min_paise = EXCLUDED.cost_min_paise,
                        cost_max_paise = EXCLUDED.cost_max_paise,
                        difficulty = EXCLUDED.difficulty,
                        best_months = EXCLUDED.best_months,
                        is_repeatable = EXCLUDED.is_repeatable,
                        source = EXCLUDED.source,
                        source_url = EXCLUDED.source_url,
                        data_confidence = EXCLUDED.data_confidence
                        -- status deliberately not touched; see the docstring.
                    RETURNING id
                    """,
                    kind,
                    poi.name,
                    poi.slug,
                    regions[poi.region],
                    lat,
                    lon,
                    poi.summary,
                    poi.description,
                    poi.duration_minutes,
                    poi.cost_band,
                    cost_min,
                    cost_max,
                    poi.difficulty,
                    poi.best_months,
                    poi.is_repeatable,
                    poi.source,
                    poi.source_url,
                    poi.confidence,
                )

                await _write_detail(conn, kind, poi_id, detail)

                # Replace tags wholesale: the file is the source of truth, so a
                # tag removed from YAML must disappear from the database.
                await conn.execute("DELETE FROM poi_tags WHERE poi_id = $1", poi_id)
                for slug, weight in poi.tags.items():
                    await conn.execute(
                        "INSERT INTO poi_tags (poi_id, tag_id, weight) VALUES ($1, $2, $3)",
                        poi_id,
                        tags[slug],
                        weight,
                    )

                report.loaded += 1
                if poi.source == PLACEHOLDER_SOURCE:
                    report.placeholders.append(poi.slug)
                if poi.confidence <= 2:
                    report.low_confidence.append(poi.slug)
                if not poi.tags:
                    report.untagged.append(poi.slug)
                if kind != "stay" and poi.duration_minutes is None:
                    report.missing_duration.append(poi.slug)
                if poi.cost_band is None:
                    report.missing_cost_band.append(poi.slug)

    log.info("seed.pois", **report.by_kind, total=report.loaded)
    return report


async def _write_detail(
    conn: asyncpg.Connection,
    kind: PoiKind,
    poi_id: Any,
    detail: PlaceDetailSeed | StayDetailSeed | ActivityDetailSeed,
) -> None:
    """Upsert the kind-specific detail row."""
    import json

    if isinstance(detail, PlaceDetailSeed):
        await conn.execute(
            """
            INSERT INTO place_details (poi_id, place_type, best_time_of_day, opening_hours,
                                       entry_fee_paise, requires_permit, notes)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (poi_id) DO UPDATE SET
                place_type = EXCLUDED.place_type,
                best_time_of_day = EXCLUDED.best_time_of_day,
                opening_hours = EXCLUDED.opening_hours,
                entry_fee_paise = EXCLUDED.entry_fee_paise,
                requires_permit = EXCLUDED.requires_permit,
                notes = EXCLUDED.notes
            """,
            poi_id,
            detail.type,
            detail.best_time_of_day,
            json.dumps(detail.opening_hours) if detail.opening_hours else None,
            detail.entry_fee_paise,
            detail.requires_permit,
            detail.notes,
        )
    elif isinstance(detail, StayDetailSeed):
        per_min, per_max = detail.per_night if detail.per_night else (None, None)
        await conn.execute(
            """
            INSERT INTO stay_details (poi_id, stay_type, per_night_min_paise,
                                      per_night_max_paise, max_occupancy, meals_included,
                                      amenities, contact)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (poi_id) DO UPDATE SET
                stay_type = EXCLUDED.stay_type,
                per_night_min_paise = EXCLUDED.per_night_min_paise,
                per_night_max_paise = EXCLUDED.per_night_max_paise,
                max_occupancy = EXCLUDED.max_occupancy,
                meals_included = EXCLUDED.meals_included,
                amenities = EXCLUDED.amenities,
                contact = EXCLUDED.contact
            """,
            poi_id,
            detail.type,
            per_min,
            per_max,
            detail.max_occupancy,
            detail.meals_included,
            json.dumps(detail.amenities),
            json.dumps(detail.contact),
        )
    else:
        await conn.execute(
            """
            INSERT INTO activity_details (poi_id, activity_type, physical_intensity, min_age,
                                          requires_guide, requires_booking, operator_name, contact)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (poi_id) DO UPDATE SET
                activity_type = EXCLUDED.activity_type,
                physical_intensity = EXCLUDED.physical_intensity,
                min_age = EXCLUDED.min_age,
                requires_guide = EXCLUDED.requires_guide,
                requires_booking = EXCLUDED.requires_booking,
                operator_name = EXCLUDED.operator_name,
                contact = EXCLUDED.contact
            """,
            poi_id,
            detail.type,
            detail.physical_intensity,
            detail.min_age,
            detail.requires_guide,
            detail.requires_booking,
            detail.operator_name,
            json.dumps(detail.contact),
        )


# ---------------------------------------------------------------------------
# Guides
# ---------------------------------------------------------------------------


async def load_guides(conn: asyncpg.Connection, seeds_dir: Path, district: str) -> int:
    """Load guides and their POI links. Returns the number of guides written."""
    import json

    path = seeds_dir / district / "guides.yaml"
    guides = [GuideSeed.model_validate(r) for r in _read_yaml_list(path)]

    tags = await _tag_ids(conn)
    regions = await _region_ids(conn)

    async with conn.transaction():
        for guide in guides:
            if guide.region not in regions:
                raise SeedError(f"{guide.slug}: unknown region {guide.region!r}")
            unknown = sorted(set(guide.tags) - set(tags))
            if unknown:
                raise SeedError(f"{guide.slug}: unknown tag slug(s) {unknown}")

            # Upsert on `slug` (added in migration 002). `contact` is written
            # empty on purpose: the seed files must not carry invented phone
            # numbers, and a reviewer fills it when the guide consents to being
            # listed.
            guide_id = await conn.fetchval(
                """
                INSERT INTO guides (slug, name, region_id, languages, contact, is_verified,
                                    day_rate_paise, source, data_confidence)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (slug) DO UPDATE SET
                    name            = EXCLUDED.name,
                    region_id       = EXCLUDED.region_id,
                    languages       = EXCLUDED.languages,
                    is_verified     = EXCLUDED.is_verified,
                    day_rate_paise  = EXCLUDED.day_rate_paise,
                    source          = EXCLUDED.source,
                    data_confidence = EXCLUDED.data_confidence
                    -- contact and status deliberately preserved on re-seed
                RETURNING id
                """,
                guide.slug,
                guide.name,
                regions[guide.region],
                guide.languages,
                json.dumps({}),
                guide.is_verified,
                guide.day_rate_paise,
                guide.source,
                guide.confidence,
            )

            await conn.execute("DELETE FROM guide_tags WHERE guide_id = $1", guide_id)
            for slug in guide.tags:
                await conn.execute(
                    "INSERT INTO guide_tags (guide_id, tag_id) VALUES ($1, $2)",
                    guide_id,
                    tags[slug],
                )

            await conn.execute("DELETE FROM poi_guides WHERE guide_id = $1", guide_id)
            for poi_slug in guide.pois:
                poi_id = await conn.fetchval("SELECT id FROM pois WHERE slug = $1", poi_slug)
                if poi_id is None:
                    raise SeedError(
                        f"{guide.slug}: links to unknown POI {poi_slug!r} — "
                        "guides load after POIs, so check the slug"
                    )
                await conn.execute(
                    "INSERT INTO poi_guides (poi_id, guide_id) VALUES ($1, $2)",
                    poi_id,
                    guide_id,
                )

    log.info("seed.guides", count=len(guides))
    return len(guides)


# ---------------------------------------------------------------------------
# The publish gate
# ---------------------------------------------------------------------------


class PublishReport(BaseModel):
    """What the gate promoted, and what it refused to."""

    promoted_pois: int = 0
    promoted_guides: int = 0
    skipped_placeholders: list[str] = Field(default_factory=list)
    skipped_low_confidence: list[str] = Field(default_factory=list)
    included_placeholders: bool = False

    def render(self) -> str:
        lines = [
            f"promoted {self.promoted_pois} POI(s) and {self.promoted_guides} guide(s) "
            "to status='published'"
        ]
        if self.skipped_placeholders:
            lines.append(
                f"  refused {len(self.skipped_placeholders)} synthetic placeholder(s): "
                + ", ".join(sorted(self.skipped_placeholders))
            )
        if self.skipped_low_confidence:
            lines.append(
                f"  refused {len(self.skipped_low_confidence)} below the confidence floor: "
                + ", ".join(sorted(self.skipped_low_confidence))
            )
        if self.included_placeholders:
            lines.append(
                "  WARNING: --include-placeholders was set. Synthetic rows are now visible "
                "to the engine. Never do this in a deployed environment."
            )
        lines.append(
            "  NOTE: published is not the same as verified. `verified_at` is still NULL on "
            "every row until a human checks it — query it to see what remains unreviewed."
        )
        return "\n".join(lines)


async def publish(
    conn: asyncpg.Connection,
    *,
    min_confidence: int = 2,
    include_placeholders: bool = False,
) -> PublishReport:
    """Promote reviewed seed rows to ``status='published'``.

    Retrieval only ever selects published rows, so this is the single gate
    between hand-compiled claims and anything a traveller sees. Two rules:

    * ``source='placeholder'`` rows are synthetic scaffolding and are refused
      unless explicitly overridden for local development.
    * rows below ``min_confidence`` are refused.

    Promotion deliberately does NOT set ``verified_at``. That column stays NULL
    until a human actually checks the row, which keeps "published but never
    verified" a queryable state rather than an invisible one.
    """
    report = PublishReport(included_placeholders=include_placeholders)

    async with conn.transaction():
        refused = await conn.fetch(
            """
            SELECT slug, source, data_confidence FROM pois
            WHERE status = 'draft'
              AND (source = $1 OR data_confidence < $2)
            """,
            PLACEHOLDER_SOURCE,
            min_confidence,
        )
        for row in refused:
            if row["source"] == PLACEHOLDER_SOURCE and not include_placeholders:
                report.skipped_placeholders.append(str(row["slug"]))
            elif int(row["data_confidence"]) < min_confidence:
                report.skipped_low_confidence.append(str(row["slug"]))

        placeholder_clause = "" if include_placeholders else "AND source <> $2"
        promoted = await conn.fetch(
            f"""
            UPDATE pois SET status = 'published'
            WHERE status = 'draft'
              AND data_confidence >= $1
              {placeholder_clause}
            RETURNING slug
            """,  # noqa: S608 — clause is a literal chosen above, not user input
            *((min_confidence,) if include_placeholders else (min_confidence, PLACEHOLDER_SOURCE)),
        )
        report.promoted_pois = len(promoted)

        guides = await conn.fetch(
            f"""
            UPDATE guides SET status = 'published'
            WHERE status = 'draft'
              AND data_confidence >= $1
              {placeholder_clause}
            RETURNING slug
            """,  # noqa: S608 — same literal clause
            *((min_confidence,) if include_placeholders else (min_confidence, PLACEHOLDER_SOURCE)),
        )
        report.promoted_guides = len(guides)

    log.info(
        "seed.publish",
        pois=report.promoted_pois,
        guides=report.promoted_guides,
        include_placeholders=include_placeholders,
    )
    return report
