# CLAUDE.md — working agreements for this repo

Read this before changing anything. The full design rationale lives in
[`docs/architecture.md`](docs/architecture.md); this file is the short version
plus the rules that are easy to break by accident.

## What this is

A trip planner for Karnataka. A user picks how to start (by Interest, by
Location, by District), answers three questions (days, people, budget), and gets
a day-by-day itinerary: route from their origin, stays, activities, places,
guides where available, and a return leg.

**Phase 1 scope (current):** *Plan by Interest* only, one district
(Chikkamagaluru), card output, static travel-time estimates, no auth, no
bookings, no marketplace.

## Domain model — use these words

| Term | Meaning |
|---|---|
| **POI** | Any point of interest. The supertype of *place*, *stay* and *activity*, in one `pois` table with a `kind` discriminator plus a 1:1 detail table per kind. |
| **place** | Something you go and see. Has a duration and usually an entry fee. |
| **stay** | Somewhere you sleep. Logistics, not an interest (see the retrieval rule below). |
| **activity** | Something you do, often guided, often bookable. |
| **interest tag** | A row in `interest_tags`. One vocabulary for the whole product, partitioned by `kind`. |
| **region** | A node in `state > district > taluk > locality`, with materialised ancestry in `regions.path`. |
| **trip request** | The immutable record of what a user asked for (`trip_requests`). |
| **brief** | `TripBrief` — the normalised, validated request the engine reads. |
| **candidate set** | The retrieved POIs a composer may choose from. The *only* legal source of places. |
| **ref** | A short candidate label (`P1`, `S3`, `A2`). What a composer refers to instead of a name or a UUID. |
| **draft** | `DraftItinerary` — a composer's output: refs, titles, prose. No facts. |
| **composer** | Whatever produced the draft: `llm` or `deterministic`. Recorded on every itinerary. |
| **leg** | One hop between two points, carrying its own `source`. |

Prefer *taluk* over "sub-district", *stay* over "hotel", *POI* over "location".

## The four hard rules

These are product constraints, not preferences. Each one is enforced by code and
covered by a test in `api/tests/test_engine.py`.

1. **A composer may only reference the candidate set.** Enforced in
   `engine/validate.py`: an unknown ref is a fatal violation → one repair
   round-trip → deterministic fallback. Never relax this into a warning.
2. **A composer may not state a fact.** `DraftItinerary` has no field for a
   coordinate, distance, duration, price, contact or time. Every fact on the
   output is hydrated in `engine/assemble.py` from the database or the routing
   provider. *Do not add factual fields to the draft models* — a test asserts
   they stay absent.
3. **Distances, durations and stop order come from a `RoutingProvider`**, never
   the model. `engine/routing.py` re-orders each day itself.
4. **The itinerary is a versioned schema.** Any breaking change to the payload
   bumps `SCHEMA_VERSION` in `domain/models.py`.

## Decisions worth not re-litigating

- **`pois` is a supertype, not three tables.** Places/stays/activities share
  ~15 columns and all need tagging; three tables would mean three tag joins,
  three retrieval queries and three code paths.
- **One `interest_tags` vocabulary, partitioned by `kind`.** Phase 5's
  marketplace adds `kind='product_category'` *rows*, not tables.
- **`regions.path` is materialised ancestry.** It makes all three planning modes
  the same query with a different `WHERE`.
- **`source` is in `travel_estimates`' primary key.** Phase 3's real ETAs land
  beside Phase 1's estimates instead of overwriting them, so the rollout can be
  partial and the two are comparable.
- **A Postgres job queue, not Celery.** Job state is transactional with the
  domain data and there is no second datastore to run.
- **lat/lon + haversine now, PostGIS later.** At 40–60 POIs per district
  haversine is instant; adding a `geography` column later is additive.
- **Money is integer paise everywhere.** Never a float.
- **`itineraries.composer` records LLM vs fallback**, so degradation is a SQL
  query rather than an invisible regression.
- **Stays are retrieved WITHOUT an interest-tag match.** You need a bed near the
  day's cluster whether or not the property is tagged `trekking`. Requiring a
  match produces itineraries with nowhere to sleep.
- **Published ≠ verified.** `publish` promotes rows but leaves `verified_at`
  NULL, so "published but never checked" stays queryable, and the itinerary
  raises an `unverified_data` warning while it is true.

## Where things go

| Change | Goes in |
|---|---|
| A planning mode's filter | `store/pois.py` — and nowhere else |
| A new LLM provider | `llm/` behind the existing port |
| A maps provider | `routing/` implementing `RoutingProvider` |
| A new fact on the output | `engine/assemble.py`, sourced from the DB |
| Seed data | `seeds/**.yaml`, then `make seed && make publish` |
| Schema change | A **new** migration; never edit an applied one |

## Conventions

- `make` with no target lists every command. `make check` (ruff + `mypy --strict`
  + pytest) must pass before a commit.
- Run quality tools via the Makefile. Invoking `pytest`/`mypy` from the repo root
  silently misses `api/pyproject.toml` and disables `asyncio_mode` and `--strict`.
- Migrations are forward-only and checksummed. Editing an applied migration makes
  the runner refuse to start — that is deliberate.
- Integration tests self-skip when Postgres is down; `make test-unit` never needs
  Docker.
- New POI rows require `source` and `data_confidence`. Guides must not carry
  invented names or contact details — a test enforces this.

## Known gaps and follow-ups

- **Re-seeding preserves `status`.** Editing a fact on an already-published row
  does not force re-verification. A `seed_hash` column plus a demote-on-change
  rule would fix it; deliberately deferred.
- **Static travel estimates are optimistic** on ghat roads. Days over the travel
  budget are flagged rather than fudged; Phase 3 replaces the provider.
- **Scheduling ignores opening hours.** `place_details.opening_hours` is stored
  but unused; the engine only warns via `late_finish`.
- **All seeded guides are synthetic placeholders**, so the guide path is only
  exercised with `tripplan publish --include-placeholders` (local dev only).

## Phase map

| Phase | Status |
|---|---|
| 1 — Plan by Interest, Chikkamagaluru, cards | in progress |
| 2 — Plan by Location / District | designed for; reuse `store/pois.py` |
| 3 — Real maps routing + map view | designed for; `RoutingProvider` + `travel_estimates.source` |
| 4 — Stay and guide booking | designed for; tables sit beside `guides` |
| 5 — Marketplace | designed for; reuses `interest_tags` + `region_id` |
