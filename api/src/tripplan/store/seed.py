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

from tripplan.domain.taxonomy import RegionKind, TagKind
from tripplan.observability.logging import get_logger

log = get_logger(__name__)


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
