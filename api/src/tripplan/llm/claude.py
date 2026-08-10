"""Composer backed by the Anthropic API.

Config-gated and NOT the default: the Anthropic API is paid, has no free tier,
and a Claude.ai subscription does not grant API access. It is here because it is
the strongest option for this task if the project ever has a budget, and because
having a third adapter proves the port is really a port.

Two implementation notes:

* Structure is enforced with **structured outputs** (`output_config.format`)
  rather than a forced tool call. For a single JSON payload this is Anthropic's
  documented approach and is stricter than tool_use — the schema constrains the
  response itself. The guarantee is identical to the other adapters'.
* `stop_reason == "refusal"` is checked before reading content. Opus 5 can
  decline via safety classifiers, returning HTTP 200 with an empty `content`, so
  indexing `content[0]` unconditionally would raise on a successful response.

The `anthropic` SDK lives in the optional `llm` extra, so it is imported lazily:
with the default configuration this module is never loaded and the dependency is
never needed.
"""

from __future__ import annotations

from dataclasses import dataclass

from tripplan.domain.models import CandidateSet, DraftItinerary, TripBrief
from tripplan.llm.base import parse_draft
from tripplan.llm.prompt import SYSTEM_PROMPT, build_user_prompt
from tripplan.llm.schema import itinerary_tool_schema
from tripplan.observability.logging import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "claude-opus-5"


@dataclass
class ClaudeComposer:
    api_key: str
    model_id: str = DEFAULT_MODEL
    timeout_seconds: float = 90.0
    max_tokens: int = 16000

    @property
    def name(self) -> str:
        return "claude"

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
        try:
            import anthropic
        except ImportError:
            log.warning(
                "llm.claude_sdk_missing",
                hint="install the optional extra: uv sync --extra llm",
            )
            return None

        client = anthropic.AsyncAnthropic(api_key=self.api_key, timeout=self.timeout_seconds)
        prompt = build_user_prompt(
            brief,
            candidates,
            day_activity_minutes=day_activity_minutes,
            repair_of=repair_of,
            violations=violations,
        )

        try:
            response = await client.messages.create(
                model=self.model_id,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                output_config={
                    # Composition is a judgement task, not a hard reasoning one;
                    # medium keeps latency and spend sane for a background job.
                    "effort": "medium",
                    "format": {
                        "type": "json_schema",
                        "schema": itinerary_tool_schema(brief.days),
                    },
                },
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            log.warning("llm.claude_unavailable", error=type(exc).__name__, detail=str(exc)[:200])
            return None
        finally:
            await client.close()

        # Check the stop reason BEFORE touching content: a refusal returns 200
        # with content empty, so content[0] would raise on a valid response.
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            log.warning("llm.claude_refusal", category=category)
            return None

        text = next(
            (block.text for block in response.content if getattr(block, "type", None) == "text"),
            None,
        )
        if not text:
            log.warning("llm.claude_empty_response", stop_reason=response.stop_reason)
            return None
        return parse_draft(text, provider=f"claude:{self.model_id}")
