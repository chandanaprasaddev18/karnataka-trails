"""FastAPI application.

The HTTP layer does as little as possible: validate the request, persist it,
enqueue a job, return a handle. Composition happens in the worker, because a
multi-step pipeline with an LLM call in the middle has no business blocking a
request.

`POST /api/plan` therefore returns **202 Accepted**, not 200 with an itinerary.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from tripplan.api.schemas import (
    AnchorOut,
    BookingOut,
    BookingRequestIn,
    DistrictOut,
    HealthOut,
    InterestOut,
    JobStatusOut,
    PlanAcceptedOut,
    PlanRequestIn,
    PlanStatusOut,
)
from tripplan.config import Settings, get_settings
from tripplan.db import apply_migrations, pool
from tripplan.engine.brief import BriefError, build_brief
from tripplan.jobs import queue
from tripplan.jobs.worker import run_worker
from tripplan.llm.factory import build_composer
from tripplan.observability.logging import configure_logging, get_logger
from tripplan.store import bookings as booking_store
from tripplan.store import market as market_store
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

        if settings.migrate_on_start:
            # A managed host has no shell to run `make migrate` from, and a schema
            # older than the code is a guaranteed 500 on the first request.
            async with db_pool.acquire() as conn:
                applied = await apply_migrations(conn, settings.migrations_dir)
            log.info("api.migrated", applied=len(applied))

        worker_task: asyncio.Task[None] | None = None
        if settings.worker.in_process:
            # One process is all a free tier gives you. See WorkerSettings.
            worker_task = asyncio.create_task(
                # Not its own signal handlers: they would replace uvicorn's and the
                # server would never shut down. Cancellation below is the mechanism.
                run_worker(settings, install_signal_handlers=False),
                name="in-process-worker",
            )
            log.info("api.worker_in_process")

        log.info("api.started", database=settings.db.safe_dsn())
        try:
            yield
        finally:
            if worker_task is not None:
                # Let the in-flight job finish rather than stranding a half-written
                # itinerary, but do not hang a deploy forever waiting for it.
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                    await asyncio.wait_for(worker_task, timeout=20)
    log.info("api.stopped")


app = FastAPI(
    title="Trip Planner API",
    version="0.1.0",
    summary="Itinerary generation over a curated Karnataka dataset.",
    lifespan=lifespan,
)

# Origins come from config so a deployment can name its own frontend without a code
# change. Never "*": these endpoints echo a session token.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    # A cross-origin response header is invisible to JavaScript unless it is
    # exposed here. Without this, the token minted for a first-time booking never
    # reached the browser: the request was stored under a token the client could
    # not learn, so "my requests" came back empty and the row was stranded.
    expose_headers=["X-Session-Token"],
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


@app.get("/api/anchors", response_model=list[AnchorOut], tags=["taxonomy"])
async def list_anchors(conn: Conn, q: str = "") -> list[AnchorOut]:
    """Places and localities a location-mode trip can be planned around.

    Server-side search rather than shipping the whole list to the browser: it will
    be every published POI in Karnataka by Phase 2's end, and `nearby` needs a
    radius count per row that only the database can do cheaply.

    An empty query returns the anchors with the most around them, which is a
    better empty state than nothing at all — most people recognise a district
    town faster than they can spell it.
    """
    rows = await poi_store.search_anchors(conn, q)
    return [
        AnchorOut(
            kind="poi" if r["kind"] == "poi" else "region",
            slug=str(r["slug"]),
            label=str(r["label"]),
            sublabel=str(r["sublabel"]),
            lat=float(r["lat"]),
            lon=float(r["lon"]),
            nearby=int(r["nearby"] or 0),
        )
        for r in rows
    ]


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

        # Which months this district can actually be planned in. A POI with no
        # best_months is open year round, so its presence makes every month viable
        # — hence the OR, rather than counting months per row.
        month_rows = await conn.fetch(
            """
            SELECT m FROM generate_series(1, 12) AS m
            WHERE EXISTS (
                SELECT 1 FROM pois p
                JOIN regions r ON r.id = p.region_id
                WHERE p.status = 'published'
                  AND p.kind IN ('place', 'activity')
                  AND r.path LIKE (SELECT path FROM regions WHERE slug = $1) || '%'
                  AND (p.best_months IS NULL OR m = ANY(p.best_months))
            )
            ORDER BY m
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
                open_months=[int(m["m"]) for m in month_rows],
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
    # Location mode: resolve the anchor slug to a point we hold, and take the
    # district from where that anchor sits rather than from the request. A caller
    # cannot plan around coordinates we have never heard of.
    anchor = None
    district_slug = payload.district
    # Interest and district modes must NAME the district. Defaulting it server-side
    # turns a client that forgot to send it into a plan for the wrong place — which
    # is exactly what happened — so the request is refused instead.
    if payload.mode != "location" and not district_slug:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="name the district to plan; see /api/districts for the ones we hold",
        )
    if payload.mode == "location":
        if not payload.anchor:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="location mode needs an anchor; pick one from /api/anchors",
            )
        resolved = await poi_store.resolve_anchor(conn, payload.anchor)
        if resolved is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"we do not have a place called '{payload.anchor}'",
            )
        anchor, district_slug = resolved

    # Guaranteed by the two branches above: named by the caller, or derived from the
    # anchor. The assert documents that for the type checker and for a reader.
    assert district_slug is not None

    try:
        brief = build_brief(
            interests=payload.interests,
            district_slug=district_slug,
            days=payload.days,
            party_size=payload.party_size,
            budget_band=payload.budget_band,
            origin_label=payload.origin,
            travel_month=payload.travel_month,
            mode=payload.mode,
            anchor=anchor,
            radius_km=payload.radius_km,
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
                "suggested_radius_km": verdict.suggested_radius_km,
                "max_days": verdict.max_days,
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


# ---------------------------------------------------------------------------
# bookings (Phase 4)
# ---------------------------------------------------------------------------
# These endpoints record a REQUEST. They cannot book anything: we hold no verified
# contact for any stay, every seeded guide is a placeholder, and there is no
# partner API or payment provider. `deliverable` on the response says so per row,
# and the database refuses `sent`/`confirmed` without a real channel.


def _booking_out(booking: booking_store.Booking) -> BookingOut:
    return BookingOut(
        id=booking.id,
        kind=booking.kind,
        status=booking.status,
        party_size=booking.party_size,
        check_in=booking.check_in,
        check_out=booking.check_out,
        note=booking.note,
        itinerary_id=booking.itinerary_id,
        day_number=booking.day_number,
        target=booking.target.model_dump(mode="json"),
        sent_via=booking.sent_via,
        created_at=booking.created_at,
        deliverable=booking.deliverable,
    )


@app.post(
    "/api/bookings",
    response_model=BookingOut,
    status_code=status.HTTP_201_CREATED,
    tags=["bookings"],
)
async def create_booking(
    payload: BookingRequestIn,
    conn: Conn,
    response: Response,
    x_session_token: Annotated[str | None, Header()] = None,
) -> BookingOut:
    """Record a booking request against a PUBLISHED stay, guide or activity.

    201, not 200: a row was created. It is deliberately not 202 — nothing is being
    processed asynchronously, because nothing is being sent anywhere.
    """
    target = await booking_store.resolve_target(conn, kind=payload.kind, slug=payload.slug)
    if target is None:
        # Draft rows are unbookable for the same reason they never reach an
        # itinerary: nobody has checked them.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"no published {payload.kind} with slug '{payload.slug}'",
        )

    session_token = x_session_token or secrets.token_urlsafe(24)
    try:
        booking_id = await booking_store.request_booking(
            conn,
            session_token=session_token,
            target=target,
            party_size=payload.party_size,
            check_in=payload.check_in,
            check_out=payload.check_out,
            note=payload.note,
            itinerary_id=payload.itinerary_id,
            day_number=payload.day_number,
        )
    except booking_store.BookingConflictError as exc:
        # 409, not a silent second row: the client already has this request open.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"message": "you already have an open request for this", "booking_id": str(exc)},
        ) from exc

    response.headers["X-Session-Token"] = session_token
    rows = await booking_store.list_bookings(conn, session_token=session_token)
    created = next(b for b in rows if b.id == booking_id)
    return _booking_out(created)


@app.get("/api/bookings", response_model=list[BookingOut], tags=["bookings"])
async def list_bookings(
    conn: Conn,
    x_session_token: Annotated[str | None, Header()] = None,
) -> list[BookingOut]:
    """This browser's requests. No token means no requests — not everyone's."""
    if not x_session_token:
        return []
    rows = await booking_store.list_bookings(conn, session_token=x_session_token)
    return [_booking_out(b) for b in rows]


@app.post("/api/bookings/{booking_id}/withdraw", response_model=BookingOut, tags=["bookings"])
async def withdraw_booking(
    booking_id: UUID,
    conn: Conn,
    x_session_token: Annotated[str | None, Header()] = None,
) -> BookingOut:
    """Withdraw a request. Scoped by session token, so one browser cannot cancel another's."""
    if not x_session_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="a session token is required")
    changed = await booking_store.withdraw(
        conn, session_token=x_session_token, booking_id=booking_id
    )
    if not changed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown or already closed request")
    rows = await booking_store.list_bookings(conn, session_token=x_session_token)
    return _booking_out(next(b for b in rows if b.id == booking_id))


# ---------------------------------------------------------------------------
# marketplace (Phase 5)
# ---------------------------------------------------------------------------


@app.get("/api/market/specialities", tags=["market"])
async def list_specialities(
    conn: Conn,
    district: str | None = None,
    itinerary_id: UUID | None = None,
) -> dict[str, Any]:
    """What the places in scope are known for producing, plus how real this is.

    `itinerary_id` narrows to the regions a trip actually passes through, read from
    `itinerary_pois` — so the take-home strip on an itinerary cannot drift from the
    stops that were planned.

    The response carries `stats` alongside the rows because the honest headline of
    this feature is a number: we list zero sellers. A page that showed six
    specialities without saying that would imply a marketplace that does not exist.
    """
    region_slugs = None
    if itinerary_id is not None:
        region_slugs = await market_store.region_slugs_for_itinerary(conn, itinerary_id)
        if not region_slugs:
            return {"specialities": [], "stats": (await market_store.stats(conn)).model_dump()}

    rows = await market_store.specialities(
        conn,
        district_slug=district if itinerary_id is None else None,
        region_slugs=region_slugs,
    )
    return {
        "specialities": [r.model_dump(mode="json") for r in rows],
        "stats": (await market_store.stats(conn)).model_dump(),
    }


@app.get("/api/market/products", tags=["market"])
async def list_products(
    conn: Conn,
    category: str | None = None,
    region: str | None = None,
) -> list[dict[str, Any]]:
    """Published products. Empty until a real vendor consents to being listed."""
    rows = await market_store.products_for(conn, category_slug=category, region_slug=region)
    return [r.model_dump(mode="json") for r in rows]
