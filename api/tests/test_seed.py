"""Seed loading and the publish gate.

The gate is the only thing standing between hand-compiled claims and something
a traveller reads, so its refusal rules are tested rather than assumed.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import asyncpg
import pytest
from pydantic import ValidationError

from tripplan.config import get_settings
from tripplan.store.seed import (
    PLACEHOLDER_SOURCE,
    GuideSeed,
    PoiSeed,
    SeedError,
    load_guides,
    load_interest_tags,
    load_pois,
    load_regions,
    publish,
)

RolledBack = Callable[[asyncpg.Connection], AbstractAsyncContextManager[None]]

DISTRICT = "chikkamagaluru"


def _seed_files() -> Path:
    return get_settings().seeds_dir


# --- seed file hygiene (no DB needed) --------------------------------------


def test_poi_seed_rejects_unknown_keys() -> None:
    """extra='forbid' turns a typo into a named error instead of a lost value."""
    with pytest.raises(ValidationError):
        PoiSeed.model_validate(
            {
                "slug": "x",
                "name": "X",
                "region": "kadur",
                "coords": [13.0, 75.0],
                "summary": "s",
                "source": "test",
                "typo_field": True,
            }
        )


def test_poi_seed_rejects_mismatched_detail_block() -> None:
    """A stay filed in places.yaml must be an error, not a mis-typed row."""
    poi = PoiSeed.model_validate(
        {
            "slug": "x",
            "name": "X",
            "region": "kadur",
            "coords": [13.0, 75.0],
            "summary": "s",
            "source": "test",
            "stay": {"type": "homestay"},
        }
    )
    with pytest.raises(SeedError, match="filed as a place"):
        poi.detail_for("place")


def test_every_seeded_guide_is_a_declared_placeholder() -> None:
    """Guides are personal data: the committed set must be synthetic, not invented people.

    If this fails, someone has added a guide with a real-looking identity. That
    needs consent and a verified contact, not a seed file entry.
    """
    import yaml

    raw = yaml.safe_load((_seed_files() / DISTRICT / "guides.yaml").read_text())
    for row in raw:
        guide = GuideSeed.model_validate(row)
        assert guide.source == PLACEHOLDER_SOURCE, (
            f"{guide.slug} is not marked as a placeholder — real guides need consent "
            "and a verified contact before they belong in the repo"
        )


# --- loading and the gate (DB) ---------------------------------------------


@pytest.mark.integration
async def test_seed_is_idempotent(db: asyncpg.Connection) -> None:
    """Reloading edited seed files must not duplicate rows."""
    seeds = _seed_files()
    await load_interest_tags(db, seeds)
    await load_regions(db, seeds)
    first = await load_pois(db, seeds, DISTRICT)
    await load_guides(db, seeds, DISTRICT)

    before = await db.fetchval("SELECT count(*) FROM pois")
    guides_before = await db.fetchval("SELECT count(*) FROM guides")

    second = await load_pois(db, seeds, DISTRICT)
    await load_guides(db, seeds, DISTRICT)

    assert await db.fetchval("SELECT count(*) FROM pois") == before
    assert await db.fetchval("SELECT count(*) FROM guides") == guides_before
    assert first.loaded == second.loaded


@pytest.mark.integration
async def test_publish_refuses_placeholders_by_default(
    db: asyncpg.Connection, rolled_back: RolledBack
) -> None:
    """Synthetic scaffolding must never become visible to the engine by accident."""
    seeds = _seed_files()
    await load_interest_tags(db, seeds)
    await load_regions(db, seeds)
    await load_pois(db, seeds, DISTRICT)

    async with rolled_back(db):
        await db.execute("UPDATE pois SET status = 'draft'")
        report = await publish(db, min_confidence=2)

        assert report.skipped_placeholders, "expected placeholder rows to be refused"
        leaked = await db.fetch(
            "SELECT slug FROM pois WHERE status = 'published' AND source = $1",
            PLACEHOLDER_SOURCE,
        )
        assert leaked == [], f"placeholders leaked into published: {[r['slug'] for r in leaked]}"


@pytest.mark.integration
async def test_publish_respects_the_confidence_floor(
    db: asyncpg.Connection, rolled_back: RolledBack
) -> None:
    seeds = _seed_files()
    await load_interest_tags(db, seeds)
    await load_regions(db, seeds)
    await load_pois(db, seeds, DISTRICT)

    async with rolled_back(db):
        await db.execute("UPDATE pois SET status = 'draft'")
        await publish(db, min_confidence=4)  # nothing in the draft set claims 4+

        published = await db.fetchval("SELECT count(*) FROM pois WHERE status = 'published'")
        assert published == 0, "a high floor should promote nothing from a hand-compiled set"


@pytest.mark.integration
async def test_publishing_does_not_claim_verification(
    db: asyncpg.Connection, rolled_back: RolledBack
) -> None:
    """`published` must stay distinguishable from `verified`, or review is unauditable."""
    seeds = _seed_files()
    await load_interest_tags(db, seeds)
    await load_regions(db, seeds)
    await load_pois(db, seeds, DISTRICT)

    async with rolled_back(db):
        await db.execute("UPDATE pois SET status = 'draft', verified_at = NULL")
        await publish(db, min_confidence=2)

        unverified = await db.fetchval(
            "SELECT count(*) FROM pois WHERE status = 'published' AND verified_at IS NULL"
        )
        assert unverified > 0, "promotion must not silently backfill verified_at"


@pytest.mark.integration
async def test_engine_visible_set_has_a_stay_and_a_place(db: asyncpg.Connection) -> None:
    """Sanity floor for step 5: retrieval cannot build a day without both."""
    seeds = _seed_files()
    await load_interest_tags(db, seeds)
    await load_regions(db, seeds)
    await load_pois(db, seeds, DISTRICT)
    await publish(db, min_confidence=2)

    counts = {
        str(r["kind"]): int(r["n"])
        for r in await db.fetch(
            "SELECT kind, count(*) AS n FROM pois WHERE status='published' GROUP BY kind"
        )
    }
    assert counts.get("place", 0) >= 10
    assert counts.get("stay", 0) >= 3
    assert counts.get("activity", 0) >= 5
