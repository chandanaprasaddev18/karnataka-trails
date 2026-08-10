"""Shared fixtures.

Integration tests self-skip when Postgres is unreachable, so `make test-unit`
works on a laptop with Docker stopped and `make test` works everywhere.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio

from tripplan.config import Settings, get_settings
from tripplan.db import apply_migrations


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


async def _try_connect(cfg: Settings) -> asyncpg.Connection | None:
    try:
        return await asyncpg.connect(dsn=cfg.db.dsn(), timeout=3)
    except (OSError, asyncpg.PostgresError, TimeoutError):
        return None


@pytest_asyncio.fixture
async def db(settings: Settings) -> AsyncIterator[asyncpg.Connection]:
    """A migrated connection, or a skip if the stack isn't running."""
    conn = await _try_connect(settings)
    if conn is None:
        pytest.skip("Postgres unreachable — run `make up` for integration tests")
    try:
        await apply_migrations(conn, settings.migrations_dir)
        yield conn
    finally:
        await conn.close()
