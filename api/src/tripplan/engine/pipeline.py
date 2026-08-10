"""The itinerary engine: stages 0-5, in one place.

    TripRequest
      [0] normalise      -> TripBrief
      [1] retrieve       -> CandidateSet          (SQL only; the only source of places)
      [2] compose        -> DraftItinerary        (LLM, if configured)
      [3] validate/repair-> DraftItinerary        (1 repair, then deterministic fallback)
      [4] route          -> RoutedPlan            (provider owns order and timing)
      [5] assemble       -> Itinerary             (every fact hydrated from the DB)

All three planning modes run this same pipeline; only stage 1's filter differs.

The composer is injected, so step 5 runs the whole pipeline with no LLM at all
and step 6 adds one without touching stages 1, 4 or 5.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import asyncpg

from tripplan.config import Settings
from tripplan.domain.models import (
    CandidateSet,
    DraftItinerary,
    GeoPoint,
    Itinerary,
    TagRef,
    TripBrief,
)
from tripplan.domain.taxonomy import ComposerName
from tripplan.engine import assemble as assemble_stage
from tripplan.engine import compose_greedy, routing
from tripplan.engine import validate as validate_stage
from tripplan.llm.base import Composer
from tripplan.observability.logging import get_logger
from tripplan.routing.static_provider import StaticEstimateProvider
from tripplan.store import pois as poi_store

log = get_logger(__name__)


class EngineError(RuntimeError):
    """Raised when the engine cannot produce an itinerary at all."""


@dataclass
class EngineResult:
    itinerary: Itinerary
    candidates: CandidateSet
    composer: ComposerName
    # Populated when an LLM draft was rejected and the fallback ran, so the
    # reason is available for logs and tests rather than being inferred.
    fallback_reason: str | None = None


async def generate(
    conn: asyncpg.Connection,
    brief: TripBrief,
    settings: Settings,
    *,
    composer: Composer | None = None,
) -> EngineResult:
    """Run the full pipeline for one brief."""

    # --- stage 1: retrieve --------------------------------------------------
    candidates = await poi_store.retrieve(
        conn,
        brief,
        max_places=settings.retrieval.max_places,
        max_stays=settings.retrieval.max_stays,
        max_activities=settings.retrieval.max_activities,
    )
    if candidates.is_empty():
        # Reuse the same diagnosis the API pre-flight uses, so a job that fails
        # here explains itself in the caller's terms rather than blaming seed
        # data that is, in almost every case, published and fine.
        verdict = await poi_store.feasibility(conn, brief)
        raise EngineError(verdict.explain())

    district = await poi_store.region_ref(conn, brief.district_slug)
    if district is None:
        raise EngineError(f"unknown district {brief.district_slug!r}")
    interests = [TagRef(**t) for t in await poi_store.interest_labels(conn, brief.tag_slugs)]

    day_minutes = settings.planning.day_activity_minutes

    # Which requested interests the candidate set simply cannot serve — almost
    # always the seasonal filter (monsoon closes the treks). Computed before
    # composition so the itinerary can say so instead of quietly delivering a
    # different trip from the one that was asked for.
    served = {tag for c in [*candidates.places, *candidates.activities] for tag in c.tags}
    unmet = [t for t in brief.tag_slugs if t not in served]

    # The provider is built before stage 2 so the composer can budget each day
    # against its approach drive.
    provider = StaticEstimateProvider(
        road_factor=settings.routing.road_factor,
        avg_speed_kmh=settings.routing.avg_speed_kmh,
    )

    def travel_minutes(a: GeoPoint, b: GeoPoint) -> int:
        return provider.leg(a, b).duration_minutes

    # --- stages 2 and 3: compose, validate, repair, fall back ---------------
    draft, used_composer, fallback_reason, validation = await _compose_and_validate(
        brief,
        candidates,
        composer,
        day_minutes=day_minutes,
        settings=settings,
        travel_minutes=travel_minutes,
    )

    # --- stage 4: route -----------------------------------------------------
    cached = await routing.load_cached_legs(conn, candidates)
    resolver = routing.LegResolver(provider, cached)
    plan = routing.route(draft, candidates, brief, resolver)
    await routing.persist_computed_legs(conn, resolver)

    # --- stage 5: assemble --------------------------------------------------
    itinerary = assemble_stage.assemble(
        plan,
        brief,
        title=draft.title,
        narrative=draft.narrative,
        composer=used_composer,
        validation=validation,
        district=district,
        interests=interests,
        day_start_time=settings.planning.day_start_time,
        max_travel_minutes_per_day=settings.routing.max_travel_minutes_per_day,
        unmet_interests=unmet,
        candidate_set_hash=candidates.fingerprint(),
        llm_provider=composer.name if composer and used_composer == "llm" else None,
        llm_model=composer.model if composer and used_composer == "llm" else None,
    )

    return EngineResult(
        itinerary=itinerary,
        candidates=candidates,
        composer=used_composer,
        fallback_reason=fallback_reason,
    )


async def _compose_and_validate(
    brief: TripBrief,
    candidates: CandidateSet,
    composer: Composer | None,
    *,
    day_minutes: int,
    settings: Settings,
    travel_minutes: Callable[[GeoPoint, GeoPoint], int],
) -> tuple[DraftItinerary, ComposerName, str | None, validate_stage.ValidationResult]:
    """Stage 2 + 3. Returns a draft that has passed fatal validation.

    Order of attempts:
      1. the LLM composer, if one is configured
      2. one repair round-trip quoting the specific violations
      3. the deterministic composer

    The deterministic result is validated too. If that ever fails fatally it is
    a bug in our own code, so it raises rather than shipping a broken itinerary.
    """
    fallback_reason: str | None = None

    if composer is not None:
        draft = await composer.compose(brief, candidates, day_activity_minutes=day_minutes)
        if draft is None:
            fallback_reason = "composer declined (not configured or unreachable)"
        else:
            result = validate_stage.validate(
                draft, candidates, brief, day_activity_minutes=day_minutes
            )
            if result.ok:
                return draft, "llm", None, result

            log.warning(
                "engine.compose.rejected",
                attempt=1,
                violations=[v.code for v in result.fatal],
            )

            for attempt in range(settings.llm.max_repair_attempts):
                repaired = await composer.compose(
                    brief,
                    candidates,
                    day_activity_minutes=day_minutes,
                    repair_of=draft,
                    violations=result.repair_brief(),
                )
                if repaired is None:
                    break
                result = validate_stage.validate(
                    repaired, candidates, brief, day_activity_minutes=day_minutes
                )
                if result.ok:
                    log.info("engine.compose.repaired", attempt=attempt + 2)
                    return repaired, "llm", None, result
                log.warning(
                    "engine.compose.rejected",
                    attempt=attempt + 2,
                    violations=[v.code for v in result.fatal],
                )

            fallback_reason = "LLM draft failed validation after repair: " + ", ".join(
                sorted({v.code for v in result.fatal})
            )

    draft = compose_greedy.compose(
        brief,
        candidates,
        day_activity_minutes=day_minutes,
        travel_minutes=travel_minutes,
    )
    result = validate_stage.validate(draft, candidates, brief, day_activity_minutes=day_minutes)
    if not result.ok:
        # Our own composer producing an invalid plan is a bug, not a data issue.
        raise EngineError(
            "the deterministic composer produced an invalid itinerary: " + result.repair_brief()
        )
    if fallback_reason:
        log.warning("engine.compose.fallback", reason=fallback_reason)
    return draft, "deterministic", fallback_reason, result
