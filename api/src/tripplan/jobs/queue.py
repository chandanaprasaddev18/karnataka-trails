"""The itinerary job queue.

A Postgres table claimed with ``FOR UPDATE SKIP LOCKED``. Two workers can run
against the same database and will never pick up the same job: the row lock is
the mutual exclusion, so there is no leader election and no second datastore.

Java analogue: Quartz's JDBC job store, minus the scheduler.

Three properties this module is responsible for:

* **Exactly-once completion.** A claim marks the row `running` and increments
  `attempts` in the same statement that locks it.
* **Recovery from a dead worker.** A `running` row whose lock is older than the
  timeout is re-queued by `reclaim_stale`. Without this, a worker killed mid-job
  strands that job forever.
* **Bounded retries.** A failure re-queues with exponential backoff until
  `max_attempts`, then parks the job as `failed` rather than looping.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg

from tripplan.db import DbConn
from tripplan.observability.logging import get_logger

log = get_logger(__name__)

# Pipeline stages, recorded on the job so a slow plan is attributable without a
# debugger. Mirrors the CHECK constraint in migration 001.
Stage = str


@dataclass
class ClaimedJob:
    job_id: UUID
    request_id: UUID
    attempts: int
    max_attempts: int

    @property
    def is_final_attempt(self) -> bool:
        return self.attempts >= self.max_attempts


async def enqueue(conn: DbConn, request_id: UUID) -> UUID:
    job_id = await conn.fetchval(
        "INSERT INTO itinerary_jobs (request_id) VALUES ($1) RETURNING id",
        request_id,
    )
    log.info("job.enqueued", job_id=str(job_id), request_id=str(request_id))
    return UUID(str(job_id))


async def claim(conn: DbConn, worker_id: str) -> ClaimedJob | None:
    """Atomically take the next runnable job, or None if the queue is empty.

    SKIP LOCKED is what makes this safe to run from N workers concurrently:
    a row already locked by another claim is skipped rather than waited on.
    """
    row = await conn.fetchrow(
        """
        WITH next_job AS (
            SELECT id FROM itinerary_jobs
            WHERE status = 'queued' AND run_after <= now()
            ORDER BY run_after, created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE itinerary_jobs j
        SET status = 'running',
            locked_at = now(),
            locked_by = $1,
            attempts = j.attempts + 1,
            stage = 'retrieval',
            error_code = NULL,
            error_detail = NULL
        FROM next_job
        WHERE j.id = next_job.id
        RETURNING j.id, j.request_id, j.attempts, j.max_attempts
        """,
        worker_id,
    )
    if row is None:
        return None
    job = ClaimedJob(
        job_id=UUID(str(row["id"])),
        request_id=UUID(str(row["request_id"])),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
    )
    log.info("job.claimed", job_id=str(job.job_id), attempt=job.attempts, worker=worker_id)
    return job


async def set_stage(conn: DbConn, job_id: UUID, stage: Stage) -> None:
    await conn.execute("UPDATE itinerary_jobs SET stage = $2 WHERE id = $1", job_id, stage)


async def succeed(conn: DbConn, job_id: UUID) -> None:
    await conn.execute(
        """
        UPDATE itinerary_jobs
        SET status = 'succeeded', stage = NULL, locked_at = NULL, locked_by = NULL
        WHERE id = $1
        """,
        job_id,
    )
    log.info("job.succeeded", job_id=str(job_id))


async def fail(
    conn: DbConn,
    job: ClaimedJob,
    *,
    code: str,
    detail: str,
    permanent: bool = False,
) -> bool:
    """Record a failure. Returns True if the job will be retried.

    A job that has exhausted its attempts is parked as `failed` rather than
    re-queued: an itinerary that fails three times will fail a fourth, and a
    hot-looping poison job is worse than a visible dead one.

    `permanent=True` parks it immediately, for failures where a retry provably
    cannot help — no candidate places, a deleted request. `attempts` is left at
    its true value rather than inflated to the maximum, so the record still says
    honestly how many times the job actually ran.
    """
    retry = not permanent and not job.is_final_attempt
    if retry:
        # Exponential backoff: 10s, 20s, 40s...
        backoff = 10 * (2 ** (job.attempts - 1))
        await conn.execute(
            """
            UPDATE itinerary_jobs
            SET status = 'queued',
                stage = NULL,
                locked_at = NULL,
                locked_by = NULL,
                run_after = now() + make_interval(secs => $2),
                error_code = $3,
                error_detail = $4
            WHERE id = $1
            """,
            job.job_id,
            float(backoff),
            code,
            detail[:2000],
        )
        log.warning(
            "job.retry_scheduled",
            job_id=str(job.job_id),
            attempt=job.attempts,
            backoff_seconds=backoff,
            code=code,
        )
    else:
        await conn.execute(
            """
            UPDATE itinerary_jobs
            SET status = 'failed',
                stage = NULL,
                locked_at = NULL,
                locked_by = NULL,
                error_code = $2,
                error_detail = $3
            WHERE id = $1
            """,
            job.job_id,
            code,
            detail[:2000],
        )
        log.error(
            "job.failed",
            job_id=str(job.job_id),
            attempts=job.attempts,
            code=code,
            detail=detail[:200],
        )
    return retry


async def reclaim_stale(conn: DbConn, *, lock_timeout_seconds: int) -> list[UUID]:
    """Re-queue jobs whose worker died holding the lock.

    `attempts` is deliberately NOT reset: a job that repeatedly kills its worker
    must still hit `max_attempts` and stop, or it becomes an infinite loop that
    takes a worker down with it each time.
    """
    rows = await conn.fetch(
        """
        UPDATE itinerary_jobs
        SET status = 'queued', stage = NULL, locked_at = NULL, locked_by = NULL
        WHERE status = 'running'
          AND locked_at < now() - make_interval(secs => $1)
        RETURNING id, locked_by, attempts
        """,
        float(lock_timeout_seconds),
    )
    if rows:
        log.warning(
            "job.reclaimed",
            count=len(rows),
            jobs=[str(r["id"]) for r in rows],
            previous_workers=sorted({str(r["locked_by"]) for r in rows}),
        )
    return [UUID(str(r["id"])) for r in rows]


async def status(conn: DbConn, request_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, status, stage, attempts, max_attempts, error_code, error_detail,
               created_at, updated_at
        FROM itinerary_jobs
        WHERE request_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        request_id,
    )
