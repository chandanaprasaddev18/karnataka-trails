"""The HTTP layer.

Driven in-process over ASGI rather than against a running server, so the tests
need no port and no separate process. The lifespan runs, which means the real
connection pool is used.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest
from asgi_lifespan import LifespanManager

from tripplan.api.app import app
from tripplan.config import Settings, get_settings
from tripplan.jobs import queue
from tripplan.jobs.worker import Worker
from tripplan.llm.factory import build_composer
from tripplan.store.seed import (
    load_interest_tags,
    load_pois,
    load_regions,
    publish,
)

DISTRICT = "chikkamagaluru"


@pytest.fixture
async def seeded(db: asyncpg.Connection) -> asyncpg.Connection:
    cfg = get_settings()
    await load_interest_tags(db, cfg.seeds_dir)
    await load_regions(db, cfg.seeds_dir)
    await load_pois(db, cfg.seeds_dir, DISTRICT)
    await publish(db, min_confidence=2)
    return db


@pytest.fixture
async def client(seeded: asyncpg.Connection) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app),
            base_url="http://testserver",
        ) as http,
    ):
        yield http


_VALID_BODY = {
    "interests": ["spiritual"],
    "days": 2,
    "party_size": 2,
    "budget_band": 3,
    "origin": "Bengaluru",
    "travel_month": 11,
}


@pytest.mark.integration
async def test_health_reports_the_published_corpus(client: httpx.AsyncClient) -> None:
    """A reachable database with nothing published is degraded, not healthy."""
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] is True
    assert body["published_pois"] > 0
    assert body["status"] == "ok"


@pytest.mark.integration
async def test_interests_come_from_the_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/taxonomy/interests")
    assert response.status_code == 200
    slugs = [row["slug"] for row in response.json()]
    assert "spiritual" in slugs
    # Only user-facing interests; audience and season tags must not leak out.
    assert "family-friendly" not in slugs
    assert "monsoon" not in slugs


@pytest.mark.integration
async def test_plan_returns_202_with_a_poll_url(client: httpx.AsyncClient) -> None:
    """Generation is async, so the POST must not return an itinerary."""
    response = await client.post("/api/plan", json=_VALID_BODY)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["poll_url"] == f"/api/plan/{body['request_id']}"
    assert response.headers["location"] == body["poll_url"]
    assert body["session_token"], "a caller must get its own session token"


@pytest.mark.integration
async def test_each_caller_gets_a_distinct_session_token(client: httpx.AsyncClient) -> None:
    """A shared constant would make per-user retrieval impossible to add later."""
    first = (await client.post("/api/plan", json=_VALID_BODY)).json()
    second = (await client.post("/api/plan", json=_VALID_BODY)).json()
    assert first["session_token"] != second["session_token"]


@pytest.mark.integration
async def test_a_supplied_session_token_is_reused(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/plan", json=_VALID_BODY, headers={"X-Session-Token": "mine"})
    assert response.json()["session_token"] == "mine"


@pytest.mark.integration
async def test_travel_month_is_persisted(
    client: httpx.AsyncClient, seeded: asyncpg.Connection
) -> None:
    """Regression: the API accepted travel_month and silently dropped it.

    Without the column, the worker rebuilt the brief from created_at and planned
    a different month than the caller asked for — a November trekking request
    came back with "no places matched: trekking" because it was planned as August.
    """
    body = dict(_VALID_BODY, travel_month=11)
    request_id = (await client.post("/api/plan", json=body)).json()["request_id"]
    stored = await seeded.fetchval(
        "SELECT travel_month FROM trip_requests WHERE id = $1", request_id
    )
    assert stored == 11


@pytest.mark.integration
@pytest.mark.parametrize(
    "override",
    [
        {"interests": []},  # must pick at least one
        {"days": 0},
        {"days": 99},
        {"party_size": 0},
        {"budget_band": 9},
        {"travel_month": 13},
        {"unexpected_field": True},  # extra="forbid"
    ],
)
async def test_invalid_requests_are_rejected_before_a_job_exists(
    client: httpx.AsyncClient, override: dict[str, object]
) -> None:
    response = await client.post("/api/plan", json={**_VALID_BODY, **override})
    assert response.status_code == 422, override


@pytest.mark.integration
async def test_an_unknown_origin_is_a_422_not_a_failed_job(client: httpx.AsyncClient) -> None:
    """Better to reject at the door than to fail a job 30 seconds later."""
    response = await client.post("/api/plan", json={**_VALID_BODY, "origin": "Reykjavik"})
    assert response.status_code == 422
    assert "unknown origin" in response.json()["detail"].lower()


@pytest.mark.integration
async def test_polling_an_unknown_request_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/plan/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


@pytest.mark.integration
async def test_full_async_round_trip(
    client: httpx.AsyncClient, seeded: asyncpg.Connection, settings: Settings
) -> None:
    """POST -> queued -> worker -> succeeded, with the itinerary on the poll response."""
    # queue.claim is FIFO, so an earlier test's leftover job would be picked up
    # instead of this one. Start from an empty queue.
    await seeded.execute("DELETE FROM itinerary_jobs")

    accepted = (await client.post("/api/plan", json=_VALID_BODY)).json()
    request_id = accepted["request_id"]

    queued = (await client.get(f"/api/plan/{request_id}")).json()
    assert queued["job"]["status"] == "queued"
    assert queued["itinerary"] is None, "no itinerary should exist before the worker runs"

    # Run the job on this test's connection rather than spawning a worker process.
    job = await queue.claim(seeded, "test-worker")
    assert job is not None
    worker = Worker(settings)
    await worker.run_job(seeded, job, build_composer(settings))

    done = (await client.get(f"/api/plan/{request_id}")).json()
    assert done["job"]["status"] == "succeeded"
    assert done["itinerary_id"] is not None
    itinerary = done["itinerary"]
    assert itinerary["schema_version"] == 1
    assert len(itinerary["days"]) == _VALID_BODY["days"]
    assert itinerary["composer"] in {"llm", "deterministic"}

    # And the itinerary is fetchable directly.
    direct = await client.get(f"/api/itineraries/{done['itinerary_id']}")
    assert direct.status_code == 200
    assert direct.json()["summary"]["title"] == itinerary["summary"]["title"]


@pytest.mark.integration
async def test_an_impossible_brief_is_refused_immediately_with_alternatives(
    client: httpx.AsyncClient,
) -> None:
    """Trekking in monsoon is a real-world constraint, not a server error.

    It used to be accepted, enqueued, and failed 30 seconds later with a message
    blaming unpublished seed data. Now it is refused at the door, names the
    season as the cause, and offers months and interests that would work — so the
    dead end becomes a redirect.
    """
    body = dict(_VALID_BODY, interests=["trekking"], travel_month=8, budget_band=4)
    response = await client.post("/api/plan", json=body)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["reason"] == "out_of_season"
    assert "monsoon" in detail["message"]
    # Trekking is open October to March in this district.
    assert 11 in detail["suggested_months"]
    assert 8 not in detail["suggested_months"]
    # And something else is worth doing in August.
    slugs = {i["slug"] for i in detail["suggested_interests"]}
    assert "spiritual" in slugs
    assert "trekking" not in slugs


@pytest.mark.integration
async def test_a_single_out_of_season_interest_behaves_like_several(
    client: httpx.AsyncClient,
) -> None:
    """The bug this fixed: one interest hard-failed where two degraded gracefully.

    Asking for trekking + spiritual in August succeeds with an `interest_unmet`
    warning. Asking for trekking alone used to be a hard failure. Both are now
    coherent: the multi-interest case still plans, the single-interest case is
    refused with the same explanation rather than an opaque error.
    """
    august = dict(_VALID_BODY, travel_month=8, budget_band=4)

    both = await client.post("/api/plan", json=dict(august, interests=["trekking", "spiritual"]))
    assert both.status_code == 202, "a servable interest alongside an unservable one still plans"

    alone = await client.post("/api/plan", json=dict(august, interests=["trekking"]))
    assert alone.status_code == 422
    assert alone.json()["detail"]["reason"] == "out_of_season"


@pytest.mark.integration
async def test_too_low_a_budget_is_reported_as_budget_not_season(
    client: httpx.AsyncClient,
) -> None:
    """Blaming the wrong constraint sends the user to fix the wrong control."""
    body = dict(_VALID_BODY, interests=["wildlife"], travel_month=11, budget_band=1)
    response = await client.post("/api/plan", json=body)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["reason"] == "budget_too_low"
    assert detail["min_budget_band"] is not None
    assert "budget" in detail["message"]


@pytest.mark.integration
async def test_a_feasible_brief_is_still_accepted(client: httpx.AsyncClient) -> None:
    """The pre-flight must not become an over-eager gate on valid requests."""
    body = dict(_VALID_BODY, interests=["trekking"], travel_month=11, budget_band=4)
    assert (await client.post("/api/plan", json=body)).status_code == 202
