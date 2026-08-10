"""Hosted composer over an OpenAI-compatible chat endpoint.

This is the Phase 1 primary. It targets any provider exposing the
OpenAI-compatible `/chat/completions` shape with tool calling, which covers the
free tiers we can actually use (Groq, Google AI Studio's compatibility endpoint)
without pulling in a vendor SDK — the whole adapter is `httpx`.

Structure is enforced by a **single forced tool call**: one tool is offered and
`tool_choice` requires it, so the provider must return arguments matching the
JSON schema rather than prose that happens to contain JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from tripplan.domain.models import CandidateSet, DraftItinerary, TripBrief
from tripplan.llm.base import parse_draft
from tripplan.llm.prompt import SYSTEM_PROMPT, build_user_prompt
from tripplan.llm.schema import TOOL_DESCRIPTION, TOOL_NAME, itinerary_tool_schema
from tripplan.observability.logging import get_logger

log = get_logger(__name__)


@dataclass
class HostedComposer:
    base_url: str
    api_key: str
    model_id: str
    timeout_seconds: float = 90.0

    @property
    def name(self) -> str:
        return "hosted"

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
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": TOOL_NAME,
                        "description": TOOL_DESCRIPTION,
                        "parameters": itinerary_tool_schema(brief.days),
                    },
                }
            ],
            # Force the tool: without this the model may answer in prose and the
            # schema stops being a guarantee.
            "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}},
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            # Declining is correct here: the pipeline falls back and the itinerary
            # still ships, marked as deterministic.
            log.warning("llm.hosted_unavailable", error=type(exc).__name__, detail=str(exc)[:200])
            return None

        arguments = _extract_tool_arguments(data)
        if arguments is None:
            log.warning("llm.hosted_no_tool_call", model=self.model_id)
            return None
        return parse_draft(arguments, provider=f"hosted:{self.model_id}")


def _extract_tool_arguments(data: dict[str, Any]) -> str | None:
    choices = data.get("choices") or []
    if not choices:
        return None
    message = choices[0].get("message") or {}
    calls = message.get("tool_calls") or []
    if calls:
        arguments = calls[0].get("function", {}).get("arguments")
        if isinstance(arguments, str):
            return arguments
        if isinstance(arguments, dict):
            return json.dumps(arguments)
    # Some compatibility layers ignore tool_choice and answer in content. Accept
    # that rather than discarding an otherwise valid itinerary.
    content = message.get("content")
    return content if isinstance(content, str) else None
