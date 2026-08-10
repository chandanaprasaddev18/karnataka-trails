"""Local composer via Ollama — offline development only.

Free and private, but a small local model adheres to a nested JSON schema much
less reliably than a hosted one, so expect the deterministic fallback to fire
more often here. That is the correct outcome: the validator rejects a bad draft
either way, and `itineraries.composer` records which path produced the result.

Uses Ollama's native `/api/chat` with `format` set to the JSON schema, which
constrains decoding rather than merely requesting JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from tripplan.domain.models import CandidateSet, DraftItinerary, TripBrief
from tripplan.llm.base import parse_draft
from tripplan.llm.prompt import SYSTEM_PROMPT, build_user_prompt
from tripplan.llm.schema import itinerary_tool_schema
from tripplan.observability.logging import get_logger

log = get_logger(__name__)


@dataclass
class OllamaComposer:
    base_url: str
    model_id: str
    timeout_seconds: float = 180.0

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def model(self) -> str | None:
        return self.model_id

    async def compose(
        self,
        brief: TripBrief,
        candidates: CandidateSet,
        *,
        day_activity_minutes: int,
        repair_of: DraftItinerary | None = None,
        violations: str | None = None,
    ) -> DraftItinerary | None:
        body: dict[str, Any] = {
            "model": self.model_id,
            "stream": False,
            # Schema-constrained decoding, not a polite request for JSON.
            "format": itinerary_tool_schema(brief.days),
            "options": {"num_ctx": 8192},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_user_prompt(
                        brief,
                        candidates,
                        day_activity_minutes=day_activity_minutes,
                        repair_of=repair_of,
                        violations=violations,
                    ),
                },
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(f"{self.base_url.rstrip('/')}/api/chat", json=body)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            log.warning("llm.ollama_unavailable", error=type(exc).__name__, detail=str(exc)[:200])
            return None

        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            log.warning("llm.ollama_empty_response", model=self.model_id)
            return None
        return parse_draft(content, provider=f"ollama:{self.model_id}")
