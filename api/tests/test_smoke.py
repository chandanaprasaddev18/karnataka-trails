"""Step 1 gate: config loads, migrations are discoverable and checksum-stable."""

from __future__ import annotations

import asyncpg
import pytest

from tripplan.config import Settings, get_settings
from tripplan.db import discover_migrations


def test_settings_load() -> None:
    cfg = get_settings()
    assert cfg.db.port > 0
    assert cfg.retrieval.max_places >= 1
    assert cfg.routing.road_factor > 1.0


def test_password_is_not_in_repr() -> None:
    """A SecretStr must not leak into logs via repr()."""
    cfg = get_settings()
    assert cfg.db.password.get_secret_value() not in repr(cfg)
    assert "***" in cfg.db.safe_dsn()


def test_llm_disabled_without_credentials() -> None:
    """Steps 1-5 must run with no key set: the engine falls back, never fails."""
    cfg = Settings.model_validate({"llm": {"backend": "hosted", "api_key": ""}})
    assert cfg.llm.is_enabled() is False


def test_migrations_are_ordered_and_checksummed() -> None:
    cfg = get_settings()
    found = discover_migrations(cfg.migrations_dir)
    assert found, "expected at least one migration"
    names = [name for name, _, _ in found]
    assert names == sorted(names), "migrations must apply in filename order"
    for _, _, checksum in found:
        assert len(checksum) == 64


@pytest.mark.integration
async def test_database_has_pgvector(db: asyncpg.Connection) -> None:
    version = await db.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    assert version is not None, "pgvector should be enabled by migration 001"
