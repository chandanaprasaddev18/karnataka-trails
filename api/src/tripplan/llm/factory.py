"""Build the configured composer, or none at all.

`TRIPPLAN_LLM__BACKEND` selects the adapter, mirroring legal-rag's
`GENERATION__BACKEND`. Returning ``None`` is a first-class outcome, not an error:
with no backend configured (the default) the engine runs the deterministic
composer and still produces a complete itinerary. That is what keeps this repo
runnable on a machine with no API keys.
"""

from __future__ import annotations

from typing import assert_never

from tripplan.config import Settings
from tripplan.llm.base import Composer
from tripplan.observability.logging import get_logger

log = get_logger(__name__)

# Sensible defaults per backend, so a working setup needs only BACKEND + API_KEY.
_DEFAULT_MODELS = {
    "hosted": "llama-3.3-70b-versatile",
    "ollama": "qwen3:4b",
    "claude": "claude-opus-5",
}
_DEFAULT_BASE_URLS = {
    "hosted": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11436",
}


def build_composer(settings: Settings) -> Composer | None:
    """Return a Composer for the configured backend, or None to use the fallback."""
    cfg = settings.llm

    if cfg.backend == "none":
        return None

    if not cfg.is_enabled():
        # A configured backend with no credential is a misconfiguration worth
        # saying out loud — but not worth failing the job over.
        log.warning(
            "llm.disabled",
            backend=cfg.backend,
            reason="no API key set; falling back to the deterministic composer",
        )
        return None

    model = cfg.model or _DEFAULT_MODELS.get(cfg.backend, "")

    if cfg.backend == "hosted":
        from tripplan.llm.hosted import HostedComposer

        return HostedComposer(
            base_url=cfg.base_url or _DEFAULT_BASE_URLS["hosted"],
            api_key=cfg.api_key.get_secret_value(),
            model_id=model,
            timeout_seconds=cfg.timeout_seconds,
        )

    if cfg.backend == "ollama":
        from tripplan.llm.ollama import OllamaComposer

        return OllamaComposer(
            base_url=cfg.base_url or _DEFAULT_BASE_URLS["ollama"],
            model_id=model,
            timeout_seconds=max(cfg.timeout_seconds, 180.0),
        )

    if cfg.backend == "claude":
        from tripplan.llm.claude import ClaudeComposer

        return ClaudeComposer(
            api_key=cfg.api_key.get_secret_value(),
            model_id=model,
            timeout_seconds=cfg.timeout_seconds,
        )

    # Exhaustive: adding a backend to the LlmBackend literal without handling it
    # here is a type error, not a silent fall-through to no composer at all.
    assert_never(cfg.backend)
