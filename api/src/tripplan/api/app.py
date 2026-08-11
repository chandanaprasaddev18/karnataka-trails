"""FastAPI application.

The HTTP layer does as little as possible: validate the request, persist it,
enqueue a job, return a handle. Composition happens in the worker, because a
multi-step pipeline with an LLM call in the middle has no business blocking a
request.

`POST /api/plan` therefore returns **202 Accepted**, not 200 with an itinerary.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from tripplan.api.schemas import (
    DistrictOut,
    HealthOut,
    InterestOut,
    JobStatusOut,
    PlanAcceptedOut,
    PlanRequestIn,
    PlanStatusOut,
)
from tripplan.config import Settings, get_settings
from tripplan.db import pool
from tripplan.engine.brief import BriefError, build_brief
from tripplan.jobs import queue
from tripplan.llm.factory import build_composer
from tripplan.observability.logging import configure_logging, get_logger
from tripplan.store import pois as poi_store
from tripplan.store.itineraries import create_request, latest_for_request

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    async with pool(settings) as db_pool:
        app.state.pool = db_pool
        app.state.settings = settings
        log.info("api.started", database=settings.db.safe_dsn())
        yield
    log.info("api.stopped")


app = FastAPI(
    title="Trip Planner API",
    version="0.1.0",
    summary="Itinerary generation over a curated Karnataka dataset.",
    lifespan=lifespan,
)

# The Next.js dev server runs on :3000. Tightened per environment in deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


async def get_conn(request: Request) -> AsyncIterator[asyncpg.Connection]:
    async with request.app.state.pool.acquire() as conn:
        yield conn


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


Conn = Annotated[asyncpg.Connection, Depends(get_conn)]
Config = Annotated[Settings, Depends(get_app_settings)]


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthOut, tags=["ops"])
async def health(conn: Conn, settings: Config) -> HealthOut:
    """Liveness plus the two things that actually break in practice.

    A reachable database with zero published POIs looks healthy but cannot plan
    anything, so the count is part of the check rather than a separate dashboard.
    """
    published = 0
    database_ok = True
    try:
        published = int(
            await conn.fetchval("SELECT count(*) FROM pois WHERE status = 'published'") or 0
        )
    except asyncpg.PostgresError:
        database_ok = False

    composer = build_composer(settings)
    return HealthOut(
        status="ok" if database_ok and published > 0 else "degraded",
        database=database_ok,
        published_pois=published,
        composer=composer.name if composer else "deterministic",
    )


# ---------------------------------------------------------------------------
# taxonomy
# ---------------------------------------------------------------------------


@app.get("/api/taxonomy/interests", response_model=list[InterestOut], tags=["taxonomy"])
async def list_interests(conn: Conn) -> list[InterestOut]:
    """The interest chips for the wizard.

    Served from the database rather than hardcoded in the frontend, so adding an
    interest is a seed-file change and the two can never disagree.
    """
    rows = await conn.fetch(
        """
        SELECT slug, label, description FROM interest_tags
        WHERE kind = 'interest' AND is_active
        ORDER BY display_order, label
        """
    )

    # A representative photograph per interest: a published PLACE carrying the
    # tag, most strongly tagged first. Several candidates per tag are fetched
    # because the top pick is often shared — Kudremukh Peak is the strongest
    # candidate for both trekking and adventurous — and a grid where two cards
    # show the same image reads as a bug.
    #
    # Places only, deliberately. An activity's photo is its locality's (see the
    # fetcher's rules), and a card captioned "Trekking" showing a valley that
    # merely contains a trek would be a decorative stand-in.
    photo_rows = await conn.fetch(
        """
        SELECT tag_slug, name, photo FROM (
            SELECT it.slug AS tag_slug, p.name, p.media -> 0 AS photo,
                   row_number() OVER (
                       PARTITION BY it.slug
                       ORDER BY pt.weight DESC, p.data_confidence DESC, p.name
                   ) AS rank
            FROM interest_tags it
            JOIN poi_tags pt ON pt.tag_id = it.id
            JOIN pois p ON p.id = pt.poi_id
            WHERE it.kind = 'interest'
              AND p.status = 'published'
              AND p.kind = 'place'
              AND p.media <> '[]'::jsonb
        ) ranked
        WHERE rank <= 5
        ORDER BY tag_slug, rank
        """
    )

    # Greedy, in tag order: take the strongest candidate no other interest has
    # taken, and fall back to the strongest of all if every one is spoken for.
    # Order comes from display_order, so the tags a user sees first get first pick.
    candidates: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for r in photo_rows:
        if r["photo"]:
            candidates.setdefault(str(r["tag_slug"]), []).append(
                (dict(r["photo"]), str(r["name"]))
            )

    used: set[str] = set()
    shots: dict[str, tuple[dict[str, Any], str]] = {}
    for r in rows:
        options = candidates.get(str(r["slug"]))
        if not options:
            continue
        pick = next((o for o in options if o[1] not in used), options[0])
        used.add(pick[1])
        shots[str(r["slug"])] = pick

    out: list[InterestOut] = []
    for r in rows:
        slug = str(r["slug"])
        shot = shots.get(slug)
        out.append(
            InterestOut(
                slug=slug,
                label=str(r["label"]),
                description=r["description"],
                photo=shot[0] if shot else None,
                photo_caption=shot[1] if shot else None,
            )
        )
    return out


@app.get("/api/districts", response_model=list[DistrictOut], tags=["taxonomy"])
async def list_districts(conn: Conn) -> list[DistrictOut]:
    """Districts with published content, for the home page cards.

    Ordered by how much we can actually plan there, so the front page never leads
    with a district we have no data for. Phase 1 has one; Phase 2 adds more and
    this endpoint does not change.
    """
    rows = await conn.fetch(
        """
        SELECT d.slug, d.name, d.media,
               count(p.id) FILTER (WHERE p.kind IN ('place', 'activity')) AS places
        FROM regions d
        LEFT JOIN regions r ON r.path LIKE d.path || '%'
        LEFT JOIN pois p ON p.region_id = r.id AND p.status = 'published'
        WHERE d.kind = 'district'
        GROUP BY d.slug, d.name, d.media
        HAVING count(p.id) > 0
        ORDER BY count(p.id) DESC, d.name
        """
    )

    out: list[DistrictOut] = []
    for row in rows:
        interests = await conn.fetch(
            """
            SELECT it.label
            FROM pois p
            JOIN regions r ON r.id = p.region_id
            JOIN poi_tags pt ON pt.poi_id = p.id
            JOIN interest_tags it ON it.id = pt.tag_id
            WHERE p.status = 'published'
              AND it.kind = 'interest'
              AND r.path LIKE (SELECT path FROM regions WHERE slug = $1) || '%'
            GROUP BY it.label
            ORDER BY count(*) DESC
            LIMIT 3
            """,
            row["slug"],
        )
        # One photo per place, highest-confidence first, for the mosaic. Capped so
        # the home page does not pull the whole corpus.
        gallery_rows = await conn.fetch(
            """
            SELECT p.media -> 0 AS photo, p.name
            FROM pois p
            JOIN regions r ON r.id = p.region_id
            WHERE p.status = 'published'
              AND p.kind = 'place'
              AND p.media <> '[]'::jsonb
              AND r.path LIKE (SELECT path FROM regions WHERE slug = $1) || '%'
            ORDER BY p.data_confidence DESC, p.name
            LIMIT 8
            """,
            row["slug"],
        )

        out.append(
            DistrictOut(
                slug=str(row["slug"]),
                name=str(row["name"]),
                published_places=int(row["places"]),
                media=list(row["media"] or []),
                top_interests=[str(i["label"]) for i in interests],
                gallery=[
                    {**dict(g["photo"]), "caption": str(g["name"])}
                    for g in gallery_rows
                    if g["photo"]
                ],
            )
        )
    return out


# ---------------------------------------------------------------------------
# planning
# ---------------------------------------------------------------------------


@app.post(
    "/api/plan",
    response_model=PlanAcceptedOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["planning"],
)
async def create_plan(
    payload: PlanRequestIn,
    conn: Conn,
    response: Response,
    x_session_token: Annotated[str | None, Header()] = None,
) -> PlanAcceptedOut:
    """Accept a trip request and enqueue generation. Returns 202 with a poll URL.

    Phase 1 has no auth, but each caller still gets its own opaque session token
    rather than a shared literal: `trip_requests.session_token` is what will make
    "my trips" possible, and one constant for every browser would make that
    impossible to add later without a backfill. The client echoes the token back
    via `X-Session-Token` on subsequent requests.
    """
    try:
        brief = build_brief(
            interests=payload.interests,
            district_slug=payload.district,
            days=payload.days,
            party_size=payload.party_size,
            budget_band=payload.budget_band,
            origin_label=payload.origin,
            travel_month=payload.travel_month,
        )
    except BriefError as exc:
        # A bad origin or an empty interest list is the caller's problem, and it
        # must surface here rather than as a job that fails 30 seconds later.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    # Pre-flight: a brief that cannot possibly be planned should be answered now,
    # with alternatives, rather than enqueued so it can fail 30 seconds later.
    # The engine keeps its own guard for the case where data changes in between.
    verdict = await poi_store.feasibility(conn, brief)
    if not verdict.ok:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": verdict.explain(),
                "reason": verdict.reason,
                "asked_month": verdict.asked_month,
                "suggested_months": verdict.suggested_months,
                "suggested_interests": verdict.suggested_interests,
                "min_budget_band": verdict.min_budget_band,
            },
        )

    session_token = x_session_token or secrets.token_urlsafe(24)

    async with conn.transaction():
        # One transaction: a request without its job would never be planned, and
        # a job without its request would fail on every attempt.
        request_id = await create_request(conn, brief, session_token=session_token)
        job_id = await queue.enqueue(conn, request_id)

    poll_url = f"/api/plan/{request_id}"
    response.headers["Location"] = poll_url
    return PlanAcceptedOut(
        request_id=request_id,
        job_id=job_id,
        status="queued",
        poll_url=poll_url,
        session_token=session_token,
    )


@app.get("/api/plan/{request_id}", response_model=PlanStatusOut, tags=["planning"])
async def plan_status(request_id: UUID, conn: Conn) -> PlanStatusOut:
    """Poll a request: job state while it runs, the itinerary once it is ready."""
    job = await queue.status(conn, request_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown request")

    out = PlanStatusOut(
        request_id=request_id,
        job=JobStatusOut(
            status=job["status"],
            stage=job["stage"],
            attempts=int(job["attempts"]),
            max_attempts=int(job["max_attempts"]),
            error_code=job["error_code"],
            error_detail=job["error_detail"],
        ),
    )

    found = await latest_for_request(conn, request_id)
    if found is not None:
        itinerary_id, payload = found
        out.itinerary_id = itinerary_id
        out.itinerary = payload
    return out


@app.get("/api/itineraries/{itinerary_id}", tags=["planning"])
async def get_itinerary(itinerary_id: UUID, conn: Conn) -> dict[str, object]:
    """Fetch a specific itinerary version directly."""
    payload = await conn.fetchval("SELECT payload FROM itineraries WHERE id = $1", itinerary_id)
    if payload is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown itinerary")
    return dict(payload)
