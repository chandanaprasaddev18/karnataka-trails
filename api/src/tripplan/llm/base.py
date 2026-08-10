"""Shared composer plumbing.

Every adapter reduces to: build the prompt, ask the provider for JSON that
matches `itinerary_tool_schema`, and parse it into a `DraftItinerary`.

Parsing is deliberately strict (`extra="forbid"` on the draft models) but the
*call* is deliberately forgiving: a provider that is down, rate-limited or
returns unusable output makes the composer return ``None``, which the pipeline
treats as "declined" and falls through to the deterministic composer. An LLM
outage must degrade the itinerary, never fail the job.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import ValidationError

from tripplan.domain.models import CandidateSet, DraftItinerary, TripBrief
from tripplan.observability.logging import get_logger

log = get_logger(__name__)


class Composer(Protocol):
    """Stage 2 of the engine.

    Returning ``None`` means "declined" — unconfigured, unreachable, or the
    output was unusable. The pipeline then falls back to the deterministic
    composer, so an LLM problem degrades the itinerary instead of failing the job.
    """

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str | None: ...

    async def compose(
        self,
        brief: TripBrief,
        candidates: CandidateSet,
        *,
        day_activity_minutes: int,
        repair_of: DraftItinerary | None = None,
        violations: str | None = None,
    ) -> DraftItinerary | None: ...


def parse_draft(payload: dict[str, Any] | str, *, provider: str) -> DraftItinerary | None:
    """Turn a provider's JSON into a validated draft, or None if it is unusable."""
    raw: Any = payload
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning("llm.parse_failed", provider=provider, error=str(exc))
            return None

    if not isinstance(raw, dict):
        log.warning("llm.parse_failed", provider=provider, error="payload is not an object")
        return None

    # Providers occasionally wrap the answer. Unwrap one level of the obvious
    # shapes rather than failing a response that is otherwise correct.
    for key in ("itinerary", "result", "output"):
        if key in raw and isinstance(raw[key], dict) and "days" in raw[key]:
            raw = raw[key]
            break

    try:
        return DraftItinerary.model_validate(raw)
    except ValidationError as exc:
        log.warning(
            "llm.schema_mismatch",
            provider=provider,
            errors=[f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]],
        )
        return None
