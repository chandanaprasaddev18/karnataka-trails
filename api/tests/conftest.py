"""Shared fixtures.

Integration tests self-skip when Postgres is unreachable, so `make test-unit`
works on a laptop with Docker stopped and `make test` works everywhere.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import asyncpg
import pytest
import pytest_asyncio

from tripplan.config import Settings, get_settings
from tripplan.db import apply_migrations, init_connection


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


async def _try_connect(cfg: Settings) -> asyncpg.Connection | None:
    try:
        conn = await asyncpg.connect(dsn=cfg.db.dsn(), timeout=3)
    except (OSError, asyncpg.PostgresError, TimeoutError):
        return None
    # Same codec setup db.connect() performs. Without it, tests hit a jsonb
    # encoding error that production code never sees — a fixture that is not
    # faithful to the real connection is worse than no fixture.
    await init_connection(conn)
    return conn


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


class _RollbackError(Exception):
    """Sentinel used to unwind a transaction without leaving rows behind."""


@pytest.fixture
def rolled_back() -> Callable[[asyncpg.Connection], AbstractAsyncContextManager[None]]:
    """Run a block in a transaction that is always rolled back.

    Lets a test insert real rows and exercise real constraints without polluting
    the database for the next test. Java analogue: @Transactional @Rollback.
    """

    @asynccontextmanager
    async def _ctx(conn: asyncpg.Connection) -> AsyncIterator[None]:
        try:
            async with conn.transaction():
                yield
                raise _RollbackError
        except _RollbackError:
            pass

    return _ctx
