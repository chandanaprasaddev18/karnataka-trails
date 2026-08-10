"""Operator CLI.

Every operation the engine supports is reachable here without the HTTP layer,
which is what makes step 5 reviewable before the API exists. Commands are added
as their step lands; `tripplan --help` lists what is available.
"""

from __future__ import annotations

import asyncio
import json

import typer

from tripplan.config import get_settings
from tripplan.db import apply_migrations, connect
from tripplan.observability.logging import configure_logging

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Trip planner operator CLI.",
)


def _bootstrap() -> None:
    cfg = get_settings()
    configure_logging(level=cfg.log_level, json_output=cfg.log_json)


@app.callback()
def main() -> None:
    """Configure logging before any command runs."""
    _bootstrap()


@app.command()
def migrate() -> None:
    """Apply pending database migrations."""

    async def _run() -> None:
        cfg = get_settings()
        async with connect(cfg) as conn:
            applied = await apply_migrations(conn, cfg.migrations_dir)
        if applied:
            typer.echo(f"applied {len(applied)} migration(s): {', '.join(applied)}")
        else:
            typer.echo("no pending migrations")

    asyncio.run(_run())


@app.command("db-info")
def db_info() -> None:
    """Show database versions and row counts."""

    async def _run() -> None:
        cfg = get_settings()
        async with connect(cfg) as conn:
            version = await conn.fetchval("SELECT version()")
            vector = await conn.fetchval(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )
            typer.echo(f"dsn      : {cfg.db.safe_dsn()}")
            typer.echo(f"postgres : {str(version).split(' on ')[0]}")
            typer.echo(f"pgvector : {vector or 'not installed'}")

            tables = await conn.fetch(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
            if not tables:
                typer.echo("tables   : none — run 'make migrate'")
                return

            typer.echo("tables   :")
            for row in tables:
                name = str(row["table_name"])
                # Table names come from information_schema, not user input.
                count = await conn.fetchval(f'SELECT count(*) FROM "{name}"')  # noqa: S608
                typer.echo(f"           {name:<22} {count:>6}")

    asyncio.run(_run())


@app.command("config-show")
def config_show() -> None:
    """Print the effective configuration with secrets masked."""
    cfg = get_settings()
    payload = json.loads(cfg.model_dump_json())
    payload["db"]["password"] = "***"
    payload["llm"]["api_key"] = "***" if cfg.llm.api_key.get_secret_value() else ""
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":  # pragma: no cover
    app()
