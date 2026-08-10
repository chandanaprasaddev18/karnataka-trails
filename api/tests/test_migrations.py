"""The migration runner's safety properties, plus the schema's load-bearing constraints.

The checksum freeze is the one guard that prevents two environments silently
ending up with different schemas, so it gets a test rather than a manual poke.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import pytest

from tripplan.config import get_settings
from tripplan.db import MigrationError, apply_migrations, discover_migrations


class _RollbackError(Exception):
    """Sentinel used to unwind a transaction without leaving rows behind."""


@asynccontextmanager
async def rolled_back(conn: asyncpg.Connection) -> AsyncIterator[None]:
    """Run a block in a transaction that is always rolled back.

    Lets a test insert real rows to exercise real constraints without polluting
    the database for the next test. Java analogue: @Transactional @Rollback.
    """
    try:
        async with conn.transaction():
            yield
            raise _RollbackError
    except _RollbackError:
        pass


@pytest.mark.integration
async def test_apply_is_idempotent(db: asyncpg.Connection) -> None:
    """The `db` fixture already migrated; a second run must be a no-op."""
    applied = await apply_migrations(db, get_settings().migrations_dir)
    assert applied == []


@pytest.mark.integration
async def test_changing_an_applied_migration_is_refused(
    db: asyncpg.Connection, tmp_path: Path
) -> None:
    """Editing a migration that has run must fail loudly, not drift silently."""
    real = get_settings().migrations_dir
    # Copy the real migrations, then tamper with the copy — never the original.
    for name, sql, _ in discover_migrations(real):
        (tmp_path / name).write_text(sql + "\n-- appended after apply\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="frozen"):
        await apply_migrations(db, tmp_path)


@pytest.mark.integration
async def test_haversine_matches_known_distance(db: asyncpg.Connection) -> None:
    """Bengaluru -> Chikkamagaluru is ~200 km straight-line."""
    km = await db.fetchval("SELECT haversine_km(12.9716, 77.5946, 13.3161, 75.7720)")
    assert km == pytest.approx(201.0, abs=2.0)


@pytest.mark.integration
async def test_region_path_must_be_slash_delimited(db: asyncpg.Connection) -> None:
    """The path CHECK is what stops a prefix match straddling a name boundary."""
    async with rolled_back(db):
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await db.execute(
                """
                INSERT INTO regions (kind, name, slug, path)
                VALUES ('state', 'Bad', 'bad-path', 'no-slashes')
                """
            )


async def _make_two_pois(conn: asyncpg.Connection) -> tuple[str, str]:
    region_id = await conn.fetchval(
        """
        INSERT INTO regions (kind, name, slug, path)
        VALUES ('district', 'T', 't-dist', '/t-dist/') RETURNING id
        """
    )
    ids: list[str] = []
    for i in (1, 2):
        poi_id = await conn.fetchval(
            """
            INSERT INTO pois (kind, name, slug, region_id, lat, lon, summary, source)
            VALUES ('place', $1, $2, $3, 13.0, 75.0, 'summary', 'test') RETURNING id
            """,
            f"P{i}",
            f"p{i}-slug",
            region_id,
        )
        ids.append(str(poi_id))
    return ids[0], ids[1]


@pytest.mark.integration
async def test_travel_estimates_allow_two_sources_per_pair(db: asyncpg.Connection) -> None:
    """The Phase 3 seam: real and estimated ETAs must coexist for the same pair."""
    async with rolled_back(db):
        first, second = await _make_two_pois(db)
        for source in ("static_haversine", "maps_api"):
            await db.execute(
                """
                INSERT INTO travel_estimates
                    (from_poi_id, to_poi_id, source, distance_km, duration_minutes)
                VALUES ($1, $2, $3, 10.0, 30)
                """,
                first,
                second,
                source,
            )
        count = await db.fetchval(
            "SELECT count(*) FROM travel_estimates WHERE from_poi_id = $1", first
        )
        assert count == 2, "source belongs in the PK so both datasets survive"


@pytest.mark.integration
async def test_cost_range_cannot_be_inverted(db: asyncpg.Connection) -> None:
    """A max below a min would render as a nonsense price range in the UI."""
    async with rolled_back(db):
        region_id = await db.fetchval(
            """
            INSERT INTO regions (kind, name, slug, path)
            VALUES ('district', 'T2', 't2-dist', '/t2-dist/') RETURNING id
            """
        )
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await db.execute(
                """
                INSERT INTO pois (kind, name, slug, region_id, lat, lon, summary, source,
                                  cost_min_paise, cost_max_paise)
                VALUES ('stay', 'Bad', 'bad-cost', $1, 13.0, 75.0, 's', 'test', 5000, 1000)
                """,
                region_id,
            )
