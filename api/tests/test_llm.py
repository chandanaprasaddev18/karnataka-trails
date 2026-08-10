"""The LLM composer layer.

No live provider is involved: these tests cover the parts that are ours — the
prompt's contents, the parsing of whatever a provider returns, the repair
round-trip, and the rule that a provider failure must degrade rather than raise.

The adapters' network calls are deliberately NOT mocked at the httpx level; the
interesting behaviour is response *shape* handling, which is tested directly
against `_extract_tool_arguments` and `parse_draft`.
"""

from __future__ import annotations

import json

import asyncpg
import pytest

from tripplan.config import Settings, get_settings
from tripplan.domain.models import (
    CandidateSet,
    DraftDay,
    DraftItem,
    DraftItinerary,
    TripBrief,
)
from tripplan.engine.brief import build_brief
from tripplan.engine.pipeline import generate
from tripplan.llm.base import parse_draft
from tripplan.llm.factory import build_composer
from tripplan.llm.hosted import _extract_tool_arguments
from tripplan.llm.prompt import build_user_prompt
from tripplan.llm.schema import itinerary_tool_schema
from tripplan.store import pois as poi_store
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
        interests=["trekking", "spiritual"],
        district_slug=DISTRICT,
        days=days,
        party_size=2,
        budget_band=3,
        origin_label="Bengaluru",
        travel_month=PEAK_MONTH,
    )


async def _candidates(conn: asyncpg.Connection, brief: TripBrief) -> CandidateSet:
    cfg = get_settings()
    return await poi_store.retrieve(
        conn,
        brief,
        max_places=cfg.retrieval.max_places,
        max_stays=cfg.retrieval.max_stays,
        max_activities=cfg.retrieval.max_activities,
    )


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------


def test_schema_pins_the_day_count() -> None:
    schema = itinerary_tool_schema(4)
    assert schema["properties"]["days"]["minItems"] == 4
    assert schema["properties"]["days"]["maxItems"] == 4


def test_schema_forbids_extra_properties_everywhere() -> None:
    """additionalProperties: false is what stops a provider smuggling a field in."""

    def walk(node: object, path: str = "$") -> list[str]:
        problems: list[str] = []
        if isinstance(node, dict):
            if node.get("type") == "object" and node.get("additionalProperties") is not False:
                problems.append(path)
            for key, value in node.items():
                problems.extend(walk(value, f"{path}.{key}"))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                problems.extend(walk(value, f"{path}[{index}]"))
        return problems

    assert walk(itinerary_tool_schema(2)) == []


# ---------------------------------------------------------------------------
# The prompt must not carry facts
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_prompt_carries_refs_but_no_facts(seeded: asyncpg.Connection) -> None:
    """If the model never sees a coordinate or a price, it cannot restate one wrong."""
    brief = _brief()
    candidates = await _candidates(seeded, brief)
    prompt = build_user_prompt(brief, candidates, day_activity_minutes=480)

    # Refs and names are present — the model needs those to choose.
    assert candidates.places[0].ref in prompt
    assert candidates.places[0].name in prompt

    # Facts are not.
    first = candidates.places[0]
    assert f"{first.point.lat}" not in prompt, "a coordinate leaked into the prompt"
    assert f"{first.point.lon}" not in prompt, "a coordinate leaked into the prompt"
    assert str(first.poi_id) not in prompt, "a UUID leaked into the prompt"
    for candidate in candidates.all():
        if candidate.cost is not None and candidate.cost.max_paise > 0:
            assert str(candidate.cost.max_paise) not in prompt, "a price leaked into the prompt"


@pytest.mark.integration
async def test_repair_prompt_quotes_the_specific_violations(seeded: asyncpg.Connection) -> None:
    brief = _brief()
    candidates = await _candidates(seeded, brief)
    draft = DraftItinerary(title="x", days=[])
    prompt = build_user_prompt(
        brief,
        candidates,
        day_activity_minutes=480,
        repair_of=draft,
        violations="- [unknown_ref] 'HAMPI1' is not in the candidate set.",
    )
    assert "REJECTED" in prompt
    assert "HAMPI1" in prompt, "the repair prompt must name the offending ref"


# ---------------------------------------------------------------------------
# Parsing whatever a provider returns
# ---------------------------------------------------------------------------

_VALID = {
    "title": "Two days",
    "narrative": "A short trip.",
    "days": [
        {
            "day_number": 1,
            "title": "Day one",
            "narrative": "n",
            "stay_ref": "S1",
            "items": [{"ref": "P1", "why_chosen": "because"}],
        }
    ],
}


def test_parse_accepts_a_dict_and_a_json_string() -> None:
    assert parse_draft(_VALID, provider="t") is not None
    assert parse_draft(json.dumps(_VALID), provider="t") is not None


def test_parse_unwraps_one_level_of_provider_wrapping() -> None:
    """Some providers wrap the answer; that is not a reason to discard a good one."""
    wrapped = {"itinerary": _VALID}
    draft = parse_draft(wrapped, provider="t")
    assert draft is not None
    assert draft.title == "Two days"


def test_parse_returns_none_on_malformed_json() -> None:
    assert parse_draft("{not json", provider="t") is None


def test_parse_returns_none_on_non_object() -> None:
    assert parse_draft("[1, 2, 3]", provider="t") is None


def test_parse_rejects_unexpected_fields() -> None:
    """extra='forbid' on the draft models keeps a provider from adding a fact."""
    payload = json.loads(json.dumps(_VALID))
    payload["days"][0]["items"][0]["distance_km"] = 42
    assert parse_draft(payload, provider="t") is None


# ---------------------------------------------------------------------------
# The hosted adapter's response shapes
# ---------------------------------------------------------------------------


def test_extracts_tool_call_arguments_as_string() -> None:
    data = {
        "choices": [
            {"message": {"tool_calls": [{"function": {"arguments": json.dumps(_VALID)}}]}}
        ]
    }
    assert _extract_tool_arguments(data) is not None


def test_extracts_tool_call_arguments_given_as_an_object() -> None:
    """Some compatibility layers return parsed arguments rather than a string."""
    data = {"choices": [{"message": {"tool_calls": [{"function": {"arguments": _VALID}}]}}]}
    extracted = _extract_tool_arguments(data)
    assert extracted is not None
    assert parse_draft(extracted, provider="t") is not None


def test_falls_back_to_content_when_tool_choice_is_ignored() -> None:
    data = {"choices": [{"message": {"content": json.dumps(_VALID)}}]}
    assert _extract_tool_arguments(data) is not None


def test_returns_none_when_there_are_no_choices() -> None:
    assert _extract_tool_arguments({"choices": []}) is None


# ---------------------------------------------------------------------------
# The factory
# ---------------------------------------------------------------------------


def test_factory_returns_none_for_the_default_backend() -> None:
    """The default config must run with no keys and no network."""
    assert build_composer(Settings.model_validate({"llm": {"backend": "none"}})) is None


def test_factory_declines_a_configured_backend_with_no_key() -> None:
    """A misconfiguration degrades to the fallback rather than failing the job."""
    settings = Settings.model_validate({"llm": {"backend": "hosted", "api_key": ""}})
    assert build_composer(settings) is None


def test_factory_builds_the_hosted_adapter_when_configured() -> None:
    settings = Settings.model_validate(
        {"llm": {"backend": "hosted", "api_key": "test-key", "model": "some-model"}}
    )
    composer = build_composer(settings)
    assert composer is not None
    assert composer.name == "hosted"
    assert composer.model == "some-model"


def test_factory_does_not_need_a_key_for_ollama() -> None:
    """Local models have no credential, so the enablement check must not demand one."""
    composer = build_composer(Settings.model_validate({"llm": {"backend": "ollama"}}))
    assert composer is not None
    assert composer.name == "ollama"


# ---------------------------------------------------------------------------
# The repair round-trip
# ---------------------------------------------------------------------------


class _RepairsOnSecondAttempt:
    """Invalid first, valid once told what was wrong.

    The existing engine test covers repair *failing*; this covers it succeeding,
    which is the path that decides whether the repair round-trip is worth having.
    """

    name = "fake"
    model = "fake-1"

    def __init__(self, candidates: CandidateSet, days: int) -> None:
        self.calls = 0
        self.saw_violations: str | None = None
        self._candidates = candidates
        self._days = days

    async def compose(
        self,
        brief: TripBrief,
        candidates: CandidateSet,
        *,
        day_activity_minutes: int,
        repair_of: DraftItinerary | None = None,
        violations: str | None = None,
    ) -> DraftItinerary:
        self.calls += 1
        if violations is not None:
            self.saw_violations = violations

        places = candidates.places
        stay = candidates.stays[0].ref if candidates.stays else None
        bad = self.calls == 1
        return DraftItinerary(
            title="repaired" if not bad else "broken",
            narrative="n",
            days=[
                DraftDay(
                    day_number=n,
                    title=f"Day {n}",
                    narrative="n",
                    stay_ref=stay,
                    items=[
                        DraftItem(
                            ref="NOT_A_REF" if bad else places[n - 1].ref,
                            why_chosen="because",
                        )
                    ],
                )
                for n in range(1, brief.days + 1)
            ],
        )


@pytest.mark.integration
async def test_a_repaired_draft_is_accepted_as_llm_output(
    seeded: asyncpg.Connection, settings: Settings
) -> None:
    brief = _brief(days=2)
    candidates = await _candidates(seeded, brief)
    composer = _RepairsOnSecondAttempt(candidates, brief.days)

    result = await generate(seeded, brief, settings, composer=composer)

    assert composer.calls == 2, "should have taken exactly one repair attempt"
    assert composer.saw_violations is not None
    assert "unknown_ref" in composer.saw_violations
    assert result.composer == "llm", "a successfully repaired draft is still LLM output"
    assert result.fallback_reason is None
    assert result.itinerary.summary.title == "repaired"
    assert result.itinerary.llm_model == "fake-1"


class _AlwaysDeclines:
    """Stands in for an unreachable or rate-limited provider."""

    name = "fake"
    model = None

    async def compose(
        self,
        brief: TripBrief,
        candidates: CandidateSet,
        *,
        day_activity_minutes: int,
        repair_of: DraftItinerary | None = None,
        violations: str | None = None,
    ) -> DraftItinerary | None:
        return None


@pytest.mark.integration
async def test_provider_outage_still_produces_an_itinerary(
    seeded: asyncpg.Connection, settings: Settings
) -> None:
    """An LLM outage must degrade the itinerary, never fail the job."""
    result = await generate(seeded, _brief(), settings, composer=_AlwaysDeclines())
    assert result.composer == "deterministic"
    assert result.fallback_reason is not None
    assert len(result.itinerary.days) == 2
    codes = {w.code for w in result.itinerary.summary.warnings}
    assert "fallback_composer" in codes, "the client must be told it got the fallback"
