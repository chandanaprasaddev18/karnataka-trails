"""asyncpg connection pool and the migration runner.

Java analogue: a HikariCP DataSource plus Flyway, in one small module.

Migrations are **forward-only and checksummed**. Once a file has been applied
its content is frozen: editing it makes the runner refuse to start rather than
silently leaving two databases with different shapes. To change something,
add a new migration.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg

from tripplan.config import Settings, get_settings
from tripplan.observability.logging import get_logger

log = get_logger(__name__)

# A connection from either source. `pool.acquire()` yields a PoolConnectionProxy,
# not a Connection, and the two are distinct to the type checker even though they
# are interchangeable at runtime. Every function that takes "a connection" should
# accept both, or callers end up sprinkling casts at each acquire site.
#
# Split by TYPE_CHECKING because asyncpg's classes are only generic in the stubs:
# subscripting them at runtime raises TypeError.
if TYPE_CHECKING:
    DbConn = asyncpg.Connection[asyncpg.Record] | asyncpg.pool.PoolConnectionProxy[asyncpg.Record]
else:
    DbConn = asyncpg.Connection

_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    text        PRIMARY KEY,
    checksum    char(64)    NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """Raised when an applied migration's content has changed on disk."""


async def init_connection(conn: asyncpg.Connection) -> None:
    """Decode jsonb to Python objects instead of raw strings.

    Without this, asyncpg hands back the JSON *text* for `media`, `contact` and
    `amenities`, and every read site has to remember to json.loads() it. One
    forgotten call becomes a string rendered where a list was expected.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


@asynccontextmanager
async def pool(settings: Settings | None = None) -> AsyncIterator[asyncpg.Pool]:
    """Open an asyncpg pool for the duration of the context."""
    cfg = settings or get_settings()
    created = await asyncpg.create_pool(
        dsn=cfg.db.dsn(),
        min_size=cfg.db.min_pool_size,
        max_size=cfg.db.max_pool_size,
        init=init_connection,
    )
    if created is None:  # pragma: no cover — asyncpg only returns None on bad args
        raise RuntimeError("failed to create connection pool")
    try:
        yield created
    finally:
        await created.close()


@asynccontextmanager
async def connect(settings: Settings | None = None) -> AsyncIterator[asyncpg.Connection]:
    """Open a single connection — for CLI commands that don't need a pool."""
    cfg = settings or get_settings()
    conn = await asyncpg.connect(dsn=cfg.db.dsn())
    await init_connection(conn)
    try:
        yield conn
    finally:
        await conn.close()


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover_migrations(migrations_dir: Path) -> list[tuple[str, str, str]]:
    """Return ``(filename, sql, checksum)`` sorted by filename.

    Filename order is the apply order, which is why they are numbered.
    """
    files = sorted(migrations_dir.glob("*.sql"), key=lambda p: p.name)
    out: list[tuple[str, str, str]] = []
    for path in files:
        sql = path.read_text(encoding="utf-8")
        out.append((path.name, sql, _checksum(sql)))
    return out


async def apply_migrations(conn: DbConn, migrations_dir: Path) -> list[str]:
    """Apply every pending migration. Returns the filenames applied.

    Takes `DbConn`, not a bare Connection: the API applies migrations on start-up
    from a POOL connection, and a pool hands out a proxy rather than a Connection.
    """
    await conn.execute(_MIGRATIONS_TABLE)
    rows = await conn.fetch("SELECT filename, checksum FROM schema_migrations")
    applied = {r["filename"]: r["checksum"] for r in rows}

    newly_applied: list[str] = []
    for filename, sql, checksum in discover_migrations(migrations_dir):
        if filename in applied:
            if applied[filename] != checksum:
                raise MigrationError(
                    f"{filename} has already been applied but its content has changed "
                    f"(recorded {applied[filename][:12]}, on disk {checksum[:12]}). "
                    "Migrations are frozen once applied — add a new one instead."
                )
            continue

        # Each migration runs in its own transaction: a failure leaves the
        # database at the last good migration rather than half-way through one.
        async with conn.transaction():
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO schema_migrations (filename, checksum) VALUES ($1, $2)",
                filename,
                checksum,
            )
        log.info("migration.applied", filename=filename)
        newly_applied.append(filename)

    return newly_applied
