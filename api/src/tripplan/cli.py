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
from tripplan.engine.brief import BriefError, build_brief
from tripplan.engine.pipeline import EngineError, generate
from tripplan.jobs.worker import run_once, run_worker
from tripplan.llm.factory import build_composer
from tripplan.observability.logging import configure_logging
from tripplan.render import render_text
from tripplan.store import pois as poi_store
from tripplan.store.itineraries import create_request, save_itinerary
from tripplan.store.photos import fetch_photos
from tripplan.store.seed import (
    load_guides,
    load_interest_tags,
    load_pois,
    load_regions,
    load_specialities,
    load_vendors,
)
from tripplan.store.seed import (
    publish as publish_seed,
)

# Phase 1 ships one district. When Phase 2 adds more, this becomes a required
# argument rather than a default.
DEFAULT_DISTRICT = "chikkamagaluru"

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


@app.command("seed-taxonomy")
def seed_taxonomy() -> None:
    """Load interest tags and the region hierarchy (idempotent)."""

    async def _run() -> None:
        cfg = get_settings()
        async with connect(cfg) as conn:
            tags = await load_interest_tags(conn, cfg.seeds_dir)
            regions = await load_regions(conn, cfg.seeds_dir)
        typer.echo(f"upserted {tags} interest tag(s) and {regions} region(s)")

    asyncio.run(_run())


@app.command("seed-pois")
def seed_pois(district: str = "all") -> None:
    """Load POIs, guides, vendors and specialities, as status='draft'.

    `--district all` (the default) loads every district that has a seed directory,
    which is what you want after an import. Naming one loads just that one.
    """

    async def _run() -> None:
        cfg = get_settings()
        districts = (
            sorted(
                p.name
                for p in cfg.seeds_dir.iterdir()
                if p.is_dir() and (p / "places.yaml").exists()
            )
            if district == "all"
            else [district]
        )
        if not districts:
            typer.secho(f"no seed directories under {cfg.seeds_dir}", fg=typer.colors.RED)
            raise typer.Exit(code=1)

        async with connect(cfg) as conn:
            for slug in districts:
                typer.secho(f"\n--- {slug}", fg=typer.colors.CYAN)
                report = await load_pois(conn, cfg.seeds_dir, slug)
                guides = await load_guides(conn, cfg.seeds_dir, slug)
                vendors = await load_vendors(conn, cfg.seeds_dir, slug)
                # Specialities are not gated (no seller, no price) — see the loader.
                specialities = await load_specialities(conn, cfg.seeds_dir, slug)
                typer.echo(report.render())
                if guides or vendors or specialities:
                    typer.echo(
                        f"loaded {guides} guide(s), {vendors} vendor(s), "
                        f"{specialities} speciality/ies"
                    )
        typer.echo(
            "\nAll rows are status='draft' and invisible to the engine. "
            "Fact-check, then run 'make publish'."
        )

    asyncio.run(_run())


@app.command()
def publish(
    min_confidence: int = 2,
    include_placeholders: bool = typer.Option(
        False,
        "--include-placeholders",
        help="LOCAL DEV ONLY: also publish synthetic placeholder rows.",
    ),
) -> None:
    """Promote reviewed seed rows to status='published' so the engine can see them."""

    async def _run() -> None:
        cfg = get_settings()
        async with connect(cfg) as conn:
            report = await publish_seed(
                conn,
                min_confidence=min_confidence,
                include_placeholders=include_placeholders,
            )
        typer.echo(report.render())

    asyncio.run(_run())


@app.command()
def plan(
    interests: str = typer.Option("trekking", help="Comma-separated interest slugs."),
    days: int = 3,
    people: int = 4,
    budget: int = typer.Option(3, help="Budget band, 1 (cheapest) to 5."),
    origin: str = "Bengaluru",
    district: str = DEFAULT_DISTRICT,
    month: int | None = typer.Option(None, help="Travel month 1-12; defaults to now."),
    mode: str = typer.Option(
        "interest",
        help="interest (tags filter), district (whole district), "
        "location (radius around an anchor).",
    ),
    anchor: str | None = typer.Option(
        None, help="Location mode: slug of a published place or a region to plan around."
    ),
    radius: int | None = typer.Option(None, help="Location mode: radius in km (default 60)."),
    save: bool = typer.Option(True, help="Persist the request and itinerary."),
    as_json: bool = typer.Option(False, "--json", help="Emit the raw payload instead of cards."),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Force the deterministic composer, ignoring TRIPPLAN_LLM__BACKEND. "
        "Use this to produce the baseline for a side-by-side comparison.",
    ),
) -> None:
    """Generate an itinerary on the command line."""

    async def _run() -> None:
        cfg = get_settings()
        try:
            # Location mode resolves its anchor against the database, exactly as
            # the API does, so the CLI cannot plan around a point we do not hold.
            resolved_anchor = None
            district_slug = district
            if mode == "location":
                if not anchor:
                    raise BriefError("location mode needs --anchor")
                async with connect(cfg) as conn:
                    found = await poi_store.resolve_anchor(conn, anchor)
                if found is None:
                    raise BriefError(f"no published place or region with slug '{anchor}'")
                resolved_anchor, district_slug = found

            brief = build_brief(
                interests=[i for i in interests.split(",") if i] if interests else [],
                district_slug=district_slug,
                days=days,
                party_size=people,
                budget_band=budget,
                origin_label=origin,
                travel_month=month,
                mode=mode,  # type: ignore[arg-type]  # validated by build_brief
                anchor=resolved_anchor,
                radius_km=radius,
            )
        except BriefError as exc:
            typer.secho(f"bad request: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc

        async with connect(cfg) as conn:
            if save:
                request_id = await create_request(
                    conn,
                    brief,
                    # An owner label for anonymous CLI runs, not a credential.
                    session_token="cli",  # noqa: S106
                )
                brief = brief.model_copy(update={"request_id": request_id})

            composer = None if no_llm else build_composer(cfg)
            try:
                result = await generate(conn, brief, cfg, composer=composer)
            except EngineError as exc:
                typer.secho(f"engine: {exc}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1) from exc

            if save:
                itinerary_id = await save_itinerary(conn, result.itinerary)
                result.itinerary.itinerary_id = itinerary_id

        if as_json:
            typer.echo(result.itinerary.model_dump_json(indent=2))
        else:
            typer.echo(render_text(result.itinerary))
        if result.fallback_reason:
            typer.secho(f"\nfell back: {result.fallback_reason}", fg=typer.colors.YELLOW, err=True)

    asyncio.run(_run())


@app.command()
def worker(
    once: bool = typer.Option(
        False, "--once", help="Claim and run a single job, then exit. Useful in CI."
    ),
) -> None:
    """Run the itinerary job worker."""

    async def _run() -> None:
        if once:
            job_id = await run_once()
            typer.echo(f"ran job {job_id}" if job_id else "queue empty")
        else:
            await run_worker()

    asyncio.run(_run())


@app.command("fetch-photos")
def fetch_photos_cmd(
    district: str = "all",
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Re-fetch records that already have a photo."
    ),
    limit: int | None = typer.Option(None, help="Only process this many POIs (for a quick trial)."),
) -> None:
    """Attach Creative Commons photos from Wikimedia Commons.

    A photo is only stored when the file's own metadata ties it to the place, and
    only when its licence and author can be read — attribution is a licence
    condition. Anything uncertain is left without a photo and reported.
    """

    async def _run() -> None:
        cfg = get_settings()
        async with connect(cfg) as conn:
            # Every district with published content, unless one is named. The
            # fetcher was written when there was only one district and silently
            # covered just that one after five more were imported.
            districts = (
                [
                    str(r["slug"])
                    for r in await conn.fetch(
                        """
                        SELECT DISTINCT d.slug FROM regions d
                        JOIN regions r ON r.path LIKE d.path || '%'
                        JOIN pois p ON p.region_id = r.id AND p.status = 'published'
                        WHERE d.kind = 'district'
                        ORDER BY d.slug
                        """
                    )
                ]
                if district == "all"
                else [district]
            )
            for slug in districts:
                typer.secho(f"\n--- {slug}", fg=typer.colors.CYAN)
                report = await fetch_photos(
                    conn,
                    district=slug,
                    photos_dir=cfg.photos.dir,
                    public_prefix=cfg.photos.public_prefix,
                    overwrite=overwrite,
                    limit=limit,
                )
                typer.echo(report.render())

    asyncio.run(_run())


@app.command("config-show")
def config_show() -> None:
    """Print the effective configuration with secrets masked."""
    cfg = get_settings()
    payload = json.loads(cfg.model_dump_json())
    payload["db"]["password"] = "***"  # noqa: S105 — masking, not a credential
    payload["llm"]["api_key"] = "***" if cfg.llm.api_key.get_secret_value() else ""
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":  # pragma: no cover
    app()
