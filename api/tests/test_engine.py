"""The engine's guarantees.

These are the tests that matter most in the whole suite. They do not check that
the itinerary is *nice* — they check the four properties the product promised:

1. the composer cannot introduce a place that is not in our database
2. the composer cannot state a fact
3. routing, not the composer, decides the order of stops
4. the payload matches its declared schema_version

Every one of them is written so that it fails if someone later "simplifies" the
validation away.
"""

from __future__ import annotations

import asyncpg
import pytest

from tripplan.config import Settings, get_settings
from tripplan.domain.models import (
    SCHEMA_VERSION,
    CandidateSet,
    DraftDay,
    DraftItem,
    DraftItinerary,
    Itinerary,
    TripBrief,
)
from tripplan.engine import compose_greedy
from tripplan.engine.brief import build_brief
from tripplan.engine.pipeline import EngineError, generate
from tripplan.engine.routing import LegResolver, route
from tripplan.engine.validate import validate
from tripplan.routing.base import haversine_km
from tripplan.routing.static_provider import StaticEstimateProvider
from tripplan.store import pois as poi_store
from tripplan.store.itineraries import create_request, save_itinerary
from tripplan.store.seed import (
    load_guides,
    load_interest_tags,
    load_pois,
    load_regions,
    publish,
)

DISTRICT = "chikkamagaluru"
# November: peak season, so the seasonal filter does not empty the candidate set.
PEAK_MONTH = 11


@pytest.fixture
async def seeded(db: asyncpg.Connection) -> asyncpg.Connection:
    cfg = get_settings()
    await load_interest_tags(db, cfg.seeds_dir)
    await load_regions(db, cfg.seeds_dir)
    await load_pois(db, cfg.seeds_dir, DISTRICT)
    await load_guides(db, cfg.seeds_dir, DISTRICT)
    await publish(db, min_confidence=2)
    return db


def _brief(days: int = 3, interests: tuple[str, ...] = ("trekking", "spiritual")) -> TripBrief:
    return build_brief(
        interests=list(interests),
        district_slug=DISTRICT,
        days=days,
        party_size=4,
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
# Guarantee 1 — the composer cannot invent a place
# ---------------------------------------------------------------------------


class _HallucinatingComposer:
    """A composer that references a place which does not exist.

    This is the exact failure the product constraint exists to prevent, so it is
    simulated rather than hoped against.
    """

    name = "fake"
    model = "fake-1"

    def __init__(self) -> None:
        self.calls = 0

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
        real = candidates.places[0].ref
        return DraftItinerary(
            title="A trip to somewhere that does not exist",
            days=[
                DraftDay(
                    day_number=n,
                    title=f"Day {n}",
                    # "HAMPI1" is not in the candidate set at all.
                    items=[DraftItem(ref=real), DraftItem(ref="HAMPI1")],
                )
                for n in range(1, brief.days + 1)
            ],
        )


@pytest.mark.integration
async def test_unknown_ref_is_rejected(seeded: asyncpg.Connection) -> None:
    brief = _brief()
    candidates = await _candidates(seeded, brief)
    draft = DraftItinerary(
        title="x",
        days=[
            DraftDay(day_number=1, title="d1", items=[DraftItem(ref="NOPE")]),
        ],
    )
    result = validate(draft, candidates, brief, day_activity_minutes=480)
    assert not result.ok
    assert any(v.code == "unknown_ref" and v.ref == "NOPE" for v in result.fatal)


@pytest.mark.integration
async def test_hallucinated_place_never_reaches_the_itinerary(
    seeded: asyncpg.Connection, settings: Settings
) -> None:
    """The whole point: a bad draft degrades to the fallback, it does not ship."""
    brief = _brief()
    composer = _HallucinatingComposer()

    result = await generate(seeded, brief, settings, composer=composer)

    assert result.composer == "deterministic", "a hallucinated ref must not be accepted"
    assert result.fallback_reason is not None
    assert "unknown_ref" in result.fallback_reason
    # It tried the repair round-trip before giving up.
    assert composer.calls == 1 + settings.llm.max_repair_attempts

    names = {item.name for day in result.itinerary.days for item in day.items}
    assert "HAMPI1" not in names

    # Every rendered POI resolves to a real published row.
    for poi_id, _, _ in result.itinerary.referenced_poi_ids():
        exists = await seeded.fetchval(
            "SELECT count(*) FROM pois WHERE id = $1 AND status = 'published'", poi_id
        )
        assert exists == 1, f"{poi_id} is not a published POI"


# ---------------------------------------------------------------------------
# Guarantee 2 — the composer cannot state a fact
# ---------------------------------------------------------------------------


def test_draft_schema_has_no_factual_fields() -> None:
    """Structural check: there is nowhere for a composer to put a fact.

    If someone adds `lat` or `price` to DraftItem, the "model cannot emit a fact"
    guarantee quietly stops being structural. This test is that tripwire.
    """
    forbidden = {
        "lat",
        "lon",
        "point",
        "distance_km",
        "duration_minutes",
        "cost",
        "price",
        "contact",
        "phone",
        "start_time_estimate",
    }
    for model in (DraftItem, DraftDay, DraftItinerary):
        leaked = forbidden & set(model.model_fields)
        assert not leaked, f"{model.__name__} exposes factual field(s): {sorted(leaked)}"


@pytest.mark.integration
async def test_every_fact_matches_the_database(
    seeded: asyncpg.Connection, settings: Settings
) -> None:
    brief = _brief()
    result = await generate(seeded, brief, settings)

    for day in result.itinerary.days:
        for item in day.items:
            row = await seeded.fetchrow(
                "SELECT name, summary, lat, lon, cost_min_paise, cost_max_paise "
                "FROM pois WHERE id = $1",
                item.poi_id,
            )
            assert row is not None
            assert item.name == row["name"]
            assert item.summary == row["summary"]
            assert item.point.lat == pytest.approx(float(row["lat"]))
            assert item.point.lon == pytest.approx(float(row["lon"]))
            if item.cost is not None and row["cost_min_paise"] is not None:
                assert item.cost.min_paise == int(row["cost_min_paise"])


# ---------------------------------------------------------------------------
# Guarantee 3 — routing owns the ordering
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_routing_reorders_against_the_composer(seeded: asyncpg.Connection) -> None:
    """A deliberately bad ordering must come back geographically sane."""
    brief = _brief(days=1)
    candidates = await _candidates(seeded, brief)
    day_items = candidates.places[:4]
    assert len(day_items) == 4, "need several places to make ordering observable"

    # Hand the router the worst plausible order: farthest-first from the origin.
    origin = brief.origin
    worst = sorted(
        day_items,
        key=lambda c: (
            -haversine_km(
                type(c.point)(lat=origin.lat, lon=origin.lon),
                c.point,
            )
        ),
    )
    draft = DraftItinerary(
        title="deliberately mis-ordered",
        days=[
            DraftDay(
                day_number=1,
                title="d1",
                stay_ref=candidates.stays[0].ref if candidates.stays else None,
                items=[DraftItem(ref=c.ref) for c in worst],
            )
        ],
    )

    resolver = LegResolver(StaticEstimateProvider())
    plan = route(draft, candidates, brief, resolver)
    produced = [i.candidate.ref for i in plan.days[0].items]

    # The router must not simply preserve what it was handed.
    assert produced != [c.ref for c in worst] or len(worst) < 3

    # And the result must be a nearest-neighbour walk from the origin.
    cursor = type(day_items[0].point)(lat=origin.lat, lon=origin.lon)
    remaining = list(day_items)
    expected: list[str] = []
    while remaining:
        nearest = min(remaining, key=lambda c: haversine_km(cursor, c.point))
        expected.append(nearest.ref)
        remaining.remove(nearest)
        cursor = nearest.point
    assert produced == expected


# ---------------------------------------------------------------------------
# Guarantee 4 — the payload matches its schema, and persists
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_itinerary_roundtrips_through_the_database(
    seeded: asyncpg.Connection, settings: Settings
) -> None:
    brief = _brief()
    request_id = await create_request(seeded, brief, session_token="test")
    brief = brief.model_copy(update={"request_id": request_id})

    result = await generate(seeded, brief, settings)
    itinerary_id = await save_itinerary(seeded, result.itinerary)

    row = await seeded.fetchrow(
        "SELECT schema_version, payload, composer FROM itineraries WHERE id = $1", itinerary_id
    )
    assert row is not None
    assert int(row["schema_version"]) == SCHEMA_VERSION
    # Re-parsing proves the stored payload is a valid Itinerary, not just JSON.
    reparsed = Itinerary.model_validate(row["payload"])
    assert reparsed.summary.title == result.itinerary.summary.title
    assert len(reparsed.days) == brief.days

    # The audit trail must cover every rendered POI.
    audited = await seeded.fetchval(
        "SELECT count(*) FROM itinerary_pois WHERE itinerary_id = $1", itinerary_id
    )
    assert audited == len(result.itinerary.referenced_poi_ids())


# ---------------------------------------------------------------------------
# The deterministic composer must never produce an invalid plan
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("days", [1, 2, 3, 5, 7])
async def test_deterministic_composer_is_always_valid(
    seeded: asyncpg.Connection, days: int
) -> None:
    """Including when days exceed the number of available clusters."""
    brief = _brief(days=days)
    candidates = await _candidates(seeded, brief)
    draft = compose_greedy.compose(brief, candidates, day_activity_minutes=480)
    result = validate(draft, candidates, brief, day_activity_minutes=480)
    assert result.ok, result.repair_brief()
    assert len(draft.days) == days


@pytest.mark.integration
async def test_no_poi_repeats_across_days(seeded: asyncpg.Connection, settings: Settings) -> None:
    brief = _brief(days=4)
    result = await generate(seeded, brief, settings)
    seen: list[str] = []
    for day in result.itinerary.days:
        for item in day.items:
            seen.append(str(item.poi_id))
    assert len(seen) == len(set(seen)), "a place appeared on more than one day"


@pytest.mark.integration
async def test_unmet_interest_is_reported_not_hidden(
    seeded: asyncpg.Connection, settings: Settings
) -> None:
    """Asking for trekking in monsoon must say so rather than silently substituting."""
    brief = build_brief(
        interests=["trekking", "spiritual"],
        district_slug=DISTRICT,
        days=3,
        party_size=2,
        budget_band=3,
        origin_label="Bengaluru",
        travel_month=8,  # August — trails closed
    )
    result = await generate(seeded, brief, settings)
    codes = {w.code for w in result.itinerary.summary.warnings}
    assert "interest_unmet" in codes


@pytest.mark.integration
async def test_engine_refuses_when_nothing_matches(
    seeded: asyncpg.Connection, settings: Settings
) -> None:
    """A brief with no candidates must fail loudly, not return an empty itinerary.

    The API pre-checks feasibility, so this path is the safety net for data that
    changes between the request being accepted and the worker running it. The
    message must diagnose the real blocker rather than blaming the seed data.
    """
    brief = build_brief(
        interests=["wildlife"],
        district_slug=DISTRICT,
        days=2,
        party_size=2,
        budget_band=1,  # nothing wildlife-tagged is this cheap
        origin_label="Bengaluru",
        travel_month=PEAK_MONTH,
    )
    with pytest.raises(EngineError, match="above your budget"):
        await generate(seeded, brief, settings)


@pytest.mark.integration
async def test_out_of_season_failure_names_the_season(
    seeded: asyncpg.Connection, settings: Settings
) -> None:
    """Trekking in monsoon must say so, not blame unpublished data."""
    brief = build_brief(
        interests=["trekking"],
        district_slug=DISTRICT,
        days=3,
        party_size=2,
        budget_band=4,
        origin_label="Bengaluru",
        travel_month=8,  # August — trails shut
    )
    with pytest.raises(EngineError, match="monsoon"):
        await generate(seeded, brief, settings)
