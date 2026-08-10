"""The itinerary worker.

Runs the pipeline for queued jobs. Deliberately a separate process from the API:
composition can take tens of seconds (retrieval, an LLM call, routing), which has
no business happening inside an HTTP request.

Shutdown is graceful — SIGTERM/SIGINT stop the loop after the in-flight job
finishes, so a deploy does not strand a half-written itinerary.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
from uuid import UUID

import asyncpg

from tripplan.config import Settings, get_settings
from tripplan.db import DbConn, pool
from tripplan.engine.pipeline import EngineError, generate
from tripplan.jobs import queue
from tripplan.llm.factory import build_composer
from tripplan.observability.logging import get_logger
from tripplan.store.itineraries import load_brief, save_itinerary

log = get_logger(__name__)


def worker_identity() -> str:
    """Host and pid, so `locked_by` names something a human can go and look at."""
    return f"{socket.gethostname()}:{os.getpid()}"


class Worker:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.identity = worker_identity()
        self._stopping = asyncio.Event()

    def request_stop(self) -> None:
        if not self._stopping.is_set():
            log.info("worker.stopping", worker=self.identity)
            self._stopping.set()

    async def run(self) -> None:
        """Poll until asked to stop."""
        log.info("worker.started", worker=self.identity)
        composer = build_composer(self.settings)
        if composer is not None:
            log.info("worker.composer", provider=composer.name, model=composer.model)
        else:
            log.info("worker.composer", provider="deterministic")

        async with pool(self.settings) as db_pool:
            while not self._stopping.is_set():
                async with db_pool.acquire() as conn:
                    await queue.reclaim_stale(
                        conn, lock_timeout_seconds=self.settings.worker.lock_timeout_seconds
                    )
                    job = await queue.claim(conn, self.identity)

                if job is None:
                    # Idle. A LISTEN/NOTIFY wake-up would cut latency, but at this
                    # volume a short poll is simpler and has no missed-signal mode.
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            self._stopping.wait(),
                            timeout=self.settings.worker.poll_interval_seconds,
                        )
                    continue

                async with db_pool.acquire() as conn:
                    await self.run_job(conn, job, composer)

        log.info("worker.stopped", worker=self.identity)

    async def run_job(self, conn: DbConn, job: queue.ClaimedJob, composer: object | None) -> None:
        try:
            brief = await load_brief(conn, job.request_id)
            if brief is None:
                # Unrecoverable: no amount of retrying will conjure the request.
                await queue.fail(
                    conn,
                    job,
                    code="request_missing",
                    detail=f"trip_request {job.request_id} no longer exists",
                    permanent=True,
                )
                return

            result = await generate(conn, brief, self.settings, composer=composer)  # type: ignore[arg-type]

            await queue.set_stage(conn, job.job_id, "assemble")
            itinerary_id = await save_itinerary(conn, result.itinerary)
            await queue.succeed(conn, job.job_id)

            log.info(
                "worker.job_done",
                job_id=str(job.job_id),
                itinerary_id=str(itinerary_id),
                composer=result.composer,
                fallback_reason=result.fallback_reason,
            )

        except EngineError as exc:
            # A data problem: no candidates, unknown district. Retrying will not
            # help, so burn the remaining attempts immediately rather than making
            # the user wait through a backoff schedule for the same answer.
            await queue.fail(conn, job, code="engine_error", detail=str(exc), permanent=True)
        except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
            # Transient infrastructure trouble: worth a retry.
            await queue.fail(conn, job, code=type(exc).__name__, detail=str(exc))
        except Exception as exc:
            log.exception("worker.unexpected_error", job_id=str(job.job_id))
            await queue.fail(conn, job, code="unexpected", detail=f"{type(exc).__name__}: {exc}")


async def run_worker(settings: Settings | None = None) -> None:
    worker = Worker(settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.request_stop)
    await worker.run()


async def run_once(settings: Settings | None = None) -> UUID | None:
    """Claim and run at most one job, then return. Used by tests and by `--once`."""
    cfg = settings or get_settings()
    worker = Worker(cfg)
    composer = build_composer(cfg)
    async with pool(cfg) as db_pool, db_pool.acquire() as conn:
        job = await queue.claim(conn, worker.identity)
        if job is None:
            return None
        await worker.run_job(conn, job, composer)
        return job.job_id
