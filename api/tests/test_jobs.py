"""The job queue's concurrency and recovery properties.

These are the parts that go wrong in production rather than in development: two
workers racing for the same job, a worker dying mid-job, and a poison job looping
forever. Each gets a test that actually exercises the condition.
"""

from __future__ import annotations

import asyncpg
import pytest

from tripplan.config import Settings, get_settings
from tripplan.domain.models import TripBrief
from tripplan.engine.brief import build_brief
from tripplan.jobs import queue
from tripplan.store.itineraries import create_request
from tripplan.store.seed import (
    load_interest_tags,
    load_pois,
    load_regions,
    publish,
)

DISTRICT = "chikkamagaluru"
PEAK_MONTH = 11


@pytest.fixture
async def seeded(db: asyncpg.Connection) -> asyncpg.Connection:
    cfg = get_settings()
    await load_interest_tags(db, cfg.seeds_dir)
    await load_regions(db, cfg.seeds_dir)
    await load_pois(db, cfg.seeds_dir, DISTRICT)
    await publish(db, min_confidence=2)
    return db


def _brief(days: int = 2) -> TripBrief:
    return build_brief(
        interests=["spiritual"],
        district_slug=DISTRICT,
        days=days,
        party_size=2,
        budget_band=3,
        origin_label="Bengaluru",
        travel_month=PEAK_MONTH,
    )


async def _enqueue(conn: asyncpg.Connection) -> tuple[str, str]:
    request_id = await create_request(conn, _brief(), session_token="test")
    job_id = await queue.enqueue(conn, request_id)
    return str(request_id), str(job_id)


@pytest.mark.integration
async def test_claim_marks_running_and_counts_the_attempt(seeded: asyncpg.Connection) -> None:
    await seeded.execute("DELETE FROM itinerary_jobs")
    _, job_id = await _enqueue(seeded)

    job = await queue.claim(seeded, "worker-a")
    assert job is not None
    assert str(job.job_id) == job_id
    assert job.attempts == 1

    row = await seeded.fetchrow(
        "SELECT status, locked_by, stage FROM itinerary_jobs WHERE id = $1", job.job_id
    )
    assert row is not None
    assert row["status"] == "running"
    assert row["locked_by"] == "worker-a"
    assert row["stage"] == "retrieval"


@pytest.mark.integration
async def test_an_empty_queue_returns_none(seeded: asyncpg.Connection) -> None:
    await seeded.execute("DELETE FROM itinerary_jobs")
    assert await queue.claim(seeded, "worker-a") is None


@pytest.mark.integration
async def test_two_workers_never_claim_the_same_job(
    seeded: asyncpg.Connection, settings: Settings
) -> None:
    """The whole point of FOR UPDATE SKIP LOCKED.

    Uses two real connections, because a single connection cannot demonstrate
    row-lock contention with itself.
    """
    await seeded.execute("DELETE FROM itinerary_jobs")
    await _enqueue(seeded)
    await _enqueue(seeded)

    # State the precondition explicitly: the assertions below only mean anything
    # if there are two claimable jobs at this instant. Nothing else in the suite
    # should be consuming them, and if that changes this fails with the reason
    # rather than as a confusing "job_b is None".
    queued = await seeded.fetchval("SELECT count(*) FROM itinerary_jobs WHERE status = 'queued'")
    assert queued == 2, f"expected 2 claimable jobs, found {queued}"

    conn_a = await asyncpg.connect(dsn=settings.db.dsn())
    conn_b = await asyncpg.connect(dsn=settings.db.dsn())
    try:
        # Hold a transaction open on A so its claimed row stays locked while B claims.
        tx_a = conn_a.transaction()
        await tx_a.start()
        job_a = await queue.claim(conn_a, "worker-a")

        tx_b = conn_b.transaction()
        await tx_b.start()
        job_b = await queue.claim(conn_b, "worker-b")

        await tx_a.commit()
        await tx_b.commit()

        assert job_a is not None, "worker A should have claimed one of the two jobs"
        assert job_b is not None, (
            "worker B got nothing while A held a lock — SKIP LOCKED should have "
            "handed it the other queued job instead of skipping to empty"
        )
        assert job_a.job_id != job_b.job_id, "SKIP LOCKED must hand out distinct jobs"
    finally:
        await conn_a.close()
        await conn_b.close()


@pytest.mark.integration
async def test_a_third_claim_finds_nothing_when_both_jobs_are_taken(
    seeded: asyncpg.Connection,
) -> None:
    await seeded.execute("DELETE FROM itinerary_jobs")
    await _enqueue(seeded)
    assert await queue.claim(seeded, "worker-a") is not None
    assert await queue.claim(seeded, "worker-b") is None


@pytest.mark.integration
async def test_stale_job_is_reclaimed_without_resetting_attempts(
    seeded: asyncpg.Connection,
) -> None:
    """A worker killed mid-job must not strand it — nor get infinite retries.

    Resetting `attempts` on reclaim would turn a job that crashes its worker into
    an infinite loop that takes a worker down each time round.
    """
    await seeded.execute("DELETE FROM itinerary_jobs")
    _, job_id = await _enqueue(seeded)
    job = await queue.claim(seeded, "worker-that-died")
    assert job is not None
    assert job.attempts == 1

    # Simulate the lock ageing past the timeout.
    await seeded.execute(
        "UPDATE itinerary_jobs SET locked_at = now() - interval '10 minutes' WHERE id = $1",
        job.job_id,
    )

    reclaimed = await queue.reclaim_stale(seeded, lock_timeout_seconds=300)
    assert [str(r) for r in reclaimed] == [job_id]

    row = await seeded.fetchrow(
        "SELECT status, attempts, locked_by FROM itinerary_jobs WHERE id = $1", job.job_id
    )
    assert row is not None
    assert row["status"] == "queued"
    assert row["locked_by"] is None
    assert int(row["attempts"]) == 1, "attempts must survive a reclaim"

    # And it is claimable again, now on attempt 2.
    again = await queue.claim(seeded, "worker-b")
    assert again is not None
    assert again.attempts == 2


@pytest.mark.integration
async def test_a_fresh_lock_is_not_reclaimed(seeded: asyncpg.Connection) -> None:
    await seeded.execute("DELETE FROM itinerary_jobs")
    await _enqueue(seeded)
    await queue.claim(seeded, "worker-a")
    assert await queue.reclaim_stale(seeded, lock_timeout_seconds=300) == []


@pytest.mark.integration
async def test_failures_retry_with_backoff_then_park(seeded: asyncpg.Connection) -> None:
    await seeded.execute("DELETE FROM itinerary_jobs")
    await _enqueue(seeded)

    for attempt in (1, 2):
        job = await queue.claim(seeded, "worker-a")
        assert job is not None, f"expected the job to be retryable on attempt {attempt}"
        assert job.attempts == attempt
        retried = await queue.fail(seeded, job, code="boom", detail="transient")
        assert retried is True

        row = await seeded.fetchrow(
            "SELECT status, run_after > now() AS deferred FROM itinerary_jobs WHERE id = $1",
            job.job_id,
        )
        assert row is not None
        assert row["status"] == "queued"
        assert row["deferred"] is True, "a retry must be deferred, not immediately re-runnable"
        # Skip the backoff so the test does not sleep.
        await seeded.execute(
            "UPDATE itinerary_jobs SET run_after = now() WHERE id = $1", job.job_id
        )

    # Third and final attempt.
    job = await queue.claim(seeded, "worker-a")
    assert job is not None
    assert job.attempts == 3
    assert job.is_final_attempt is True
    retried = await queue.fail(seeded, job, code="boom", detail="still broken")
    assert retried is False

    row = await seeded.fetchrow(
        "SELECT status, error_code FROM itinerary_jobs WHERE id = $1", job.job_id
    )
    assert row is not None
    assert row["status"] == "failed", "a poison job must park, not loop forever"
    assert row["error_code"] == "boom"
    assert await queue.claim(seeded, "worker-a") is None


@pytest.mark.integration
async def test_status_returns_the_most_recent_job(seeded: asyncpg.Connection) -> None:
    await seeded.execute("DELETE FROM itinerary_jobs")
    request_id = await create_request(seeded, _brief(), session_token="test")
    await queue.enqueue(seeded, request_id)
    await queue.enqueue(seeded, request_id)

    found = await queue.status(seeded, request_id)
    assert found is not None
    assert found["status"] == "queued"
