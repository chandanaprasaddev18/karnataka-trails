# Trip Planner — Architecture & Phase 1 Build Plan

## Context

Greenfield build of a production trip-planning app for Karnataka tourism. Users start
planning one of three ways (by Interest, by Location, by District); all three feed **one**
itinerary engine and differ only in how the candidate set is filtered. The engine produces a
day-by-day itinerary: optimised route from a starting point, stays, daily activities, places,
guides where available, and a return leg.

Four constraints are fixed by the product owner and drive most of this design:

1. **The LLM composes only over our own curated data** — it must never invent a place.
   Enforced in code (post-hoc candidate-ID validation), not just in the prompt.
2. **Distances, timings and stop ordering come from a real routing API**, not model judgement.
   Phase 1 uses static estimates behind the same interface the maps API will implement.
3. **Generation is an async multi-step job** (retrieve → compose → route), not a blocking request.
4. **Output is a structured JSON schema via tool_use**, so the frontend renders reliably.

Phase 1 scope: *Plan by Interest*, one district (**Chikkamagaluru**), card-based text output,
static travel-time placeholder, no auth, no bookings, no marketplace. Phases 2–5 (other modes,
real maps, bookings, marketplace) are designed for but not built.

The workspace already contains `legal-rag`, an unrelated Python/Postgres project with
conventions worth inheriting wholesale (see *Conventions inherited* below). This project is
deliberately separate — new repo, new container, new port.

---

## 1. Tech stack

| Layer | Choice | Why / tradeoff |
|---|---|---|
| Frontend | **Next.js 15 (App Router) + TypeScript + Tailwind** | Server components fetch the itinerary; a small client island holds the wizard state. Card rendering is the Phase 1 deliverable; the Phase 3 map view drops into the same page shell. |
| Backend | **FastAPI + Python 3.12 (uv)** | Matches `legal-rag` conventions exactly, so tooling and muscle memory transfer. Pydantic v2 is how we enforce the itinerary schema and validate LLM output — the same model object is the contract for the API, the DB payload, and the validator. |
| DB | **Postgres 17 + pgvector**, Docker, container `tripplan-db` on host port **5434** | 5433 is taken by `legalrag-db`. pgvector is enabled in migration 001 but unused in Phase 1 — it's there so Phase 2's free-text "Plan by Location" search is a data migration, not a schema rewrite. |
| Async jobs | **Postgres job table + `FOR UPDATE SKIP LOCKED` worker** | No Redis, no Celery, no extra container. Job state is transactional with the domain data, so a job can never claim a candidate set that rolled back. Volume is low (one job per plan request). *Java analogue: a DB-backed queue like Quartz's JDBC store, minus the scheduler.* |
| LLM | **`ItineraryComposer` port + adapters** | Primary: a free-tier hosted model with native JSON-schema/function-calling. Secondary: Ollama for offline dev. Third: Claude, config-gated. Switched by one env var, mirroring `legal-rag`'s `GENERATION__BACKEND`. |
| Routing | **`RoutingProvider` port** | Phase 1 impl: `StaticEstimateProvider` (haversine × road factor, cached in a table). Phase 3 impl: a real maps API writing to the same table with a different `source` value. |
| Geo | **Plain `lat`/`lon` + a haversine SQL function** | At 40–60 POIs per district, haversine in SQL is instant. PostGIS is deferred: adding a generated `geography` column later is an additive migration, not a rewrite. |
| Deploy | Frontend on Vercel; API + worker as two processes on Fly.io/Render; managed Postgres (Neon or Supabase — both ship pgvector) | All have usable free tiers. The API and worker share one image and differ only by entrypoint. |

### Deliberately rejected

- **Celery/Redis** — a second datastore and a second failure mode for a workload that peaks in
  the tens of jobs. Revisit if job volume or fan-out grows.
- **PostGIS in Phase 1** — pure cost, no Phase 1 benefit; additive later.
- **LLM-computed distances** — explicitly out of scope per the stated constraints.

### Conventions inherited from `legal-rag`

Follow these without re-deciding them: `uv` + `pyproject.toml` with extras gating heavy deps;
`Makefile` as the single entry point (`make` with no target prints help); forward-only
**checksummed** SQL migrations applied in filename order (Flyway semantics — once applied,
content is frozen); `ruff` + `mypy --strict`; `pydantic-settings` with `__`-nested env vars;
`structlog`; a `typer` CLI; `docker compose` with an explicit `COMPOSE_PROJECT_NAME`,
non-default host ports, and heavy services behind profiles; integration tests that self-skip
when the DB is unreachable; and design rationale written into migration headers as comments.

---

## 2. Database schema

The whole schema turns on one decision: **`pois` is a supertype table with a `kind`
discriminator**, not three parallel tables. Places, stays and activities share ~15 columns
(name, geo, region, description, duration, cost, media, provenance) and every one of them
needs tagging, so three tables would mean three tag join tables, three retrieval queries, and
three code paths in the engine. One supertype gives the engine a single uniform candidate query
and the taxonomy a single join table with real foreign keys.

*Java analogue: class-table inheritance — `@Inheritance(strategy = JOINED)`.*

### Taxonomy — shared by all three phases

```sql
interest_tags (
  id smallint PK, slug text UNIQUE, label text,
  kind text NOT NULL,          -- 'interest' | 'terrain' | 'audience' | 'season' | 'product_category'
  description text, display_order smallint, is_active boolean
)
```

One tag vocabulary, `kind`-partitioned. Phase 1 seeds `kind='interest'`
(spiritual, adventurous, trekking, …). Phase 5's marketplace adds
`kind='product_category'` rows to the **same table** and tags vendors/products against it —
that is the "no rewrite" guarantee, and it is why `kind` exists on day one.

### Geography — shared by all three modes

```sql
regions (
  id bigint PK, parent_id bigint NULL REFERENCES regions(id),
  kind text NOT NULL,          -- 'state' | 'district' | 'taluk' | 'locality'
  name text, slug text,
  path text NOT NULL,          -- materialised ancestry: '/karnataka/chikkamagaluru/mudigere/'
  centroid_lat/-lon double precision
)
-- index: (path text_pattern_ops) for descendant prefix matching
```

`path` prefix matching answers "every POI in this district, including its taluks and
localities" without a recursive CTE. This single column is what makes Phase 2's three modes the
same query with a different `WHERE`:

| Mode | Filter clause |
|---|---|
| By Interest (Phase 1) | `poi_tags.tag_id = ANY(:tags)` |
| By District (Phase 2) | `regions.path LIKE :district_path \|\| '%'` |
| By Location (Phase 2) | `haversine(poi, :anchor) < :radius_km` (+ pgvector semantic match on typed free text) |

### POI supertype + kind detail tables

```sql
pois (
  id uuid PK, kind text NOT NULL CHECK (kind IN ('place','stay','activity')),
  name text, slug text UNIQUE, region_id bigint REFERENCES regions(id),
  lat/lon double precision,
  summary text, description text,
  typical_duration_minutes integer,      -- engine uses this for day budgeting
  cost_band smallint,                    -- 1..5, comparable across kinds
  cost_min_paise/cost_max_paise bigint,  -- integer paise, never float money
  difficulty smallint NULL,
  media jsonb DEFAULT '[]',
  -- provenance: the seed set is hand-compiled, so unverified data must be visible
  source text, source_url text, data_confidence smallint, verified_at timestamptz NULL,
  status text NOT NULL DEFAULT 'draft',  -- retrieval only ever selects 'published'
  created_at/updated_at timestamptz
)

place_details    (poi_id uuid PK REFERENCES pois ON DELETE CASCADE, place_type, best_time_of_day, opening_hours jsonb, entry_fee_paise, …)
stay_details     (poi_id uuid PK …, stay_type, per_night_min_paise, per_night_max_paise, max_occupancy, amenities jsonb, contact jsonb, …)
activity_details (poi_id uuid PK …, activity_type, physical_intensity, min_age, requires_guide boolean, operator_name, …)

poi_tags (poi_id uuid REFERENCES pois, tag_id smallint REFERENCES interest_tags,
          weight smallint DEFAULT 50, PRIMARY KEY (poi_id, tag_id))
```

`weight` lets retrieval rank a hard trekking peak above a mildly scenic walk for the
`trekking` tag, instead of treating tag membership as boolean.

### Guides

```sql
guides (id uuid PK, name, region_id, languages text[], contact jsonb, is_verified boolean, …)
guide_tags (guide_id, tag_id)          -- same interest_tags vocabulary
poi_guides (poi_id, guide_id)          -- "guide details where available"
```

Phase 1 renders guide details read-only. Phase 4 booking adds tables *beside* these; it does
not alter them.

### Travel estimates — the routing seam

```sql
travel_estimates (
  from_poi_id uuid, to_poi_id uuid,
  distance_km numeric(7,2), duration_minutes integer,
  source text NOT NULL,        -- 'static_haversine' (Phase 1) | 'maps_api' (Phase 3)
  computed_at timestamptz,
  PRIMARY KEY (from_poi_id, to_poi_id, source)
)
```

`source` in the primary key is the whole trick: Phase 3 writes real ETAs alongside the
placeholders and the engine simply prefers the highest-priority available `source`. Both
datasets coexist, so real-vs-estimated routes are directly comparable and nothing is thrown away.

### Requests, jobs, itineraries

```sql
users (id uuid PK, …)                          -- exists now, empty in Phase 1

trip_requests (
  id uuid PK,
  user_id uuid NULL REFERENCES users(id),      -- nullable: auth slots in later, no migration
  session_token text,                          -- anonymous ownership in Phase 1
  mode text NOT NULL,                          -- 'interest' | 'location' | 'district'
  tag_ids smallint[], region_id bigint NULL, anchor_poi_id uuid NULL,
  days smallint, party_size smallint, budget_band smallint,
  origin_label text, origin_lat/-lon double precision,
  created_at timestamptz
)

itinerary_jobs (
  id uuid PK, request_id uuid REFERENCES trip_requests(id),
  status text NOT NULL,        -- queued | running | succeeded | failed
  stage text,                  -- retrieval | compose | route | assemble  (observability)
  attempts smallint DEFAULT 0, max_attempts smallint DEFAULT 3,
  locked_at timestamptz NULL, locked_by text NULL, run_after timestamptz DEFAULT now(),
  error_code text NULL, error_detail text NULL,
  created_at/updated_at timestamptz
)
-- index: (status, run_after) WHERE status = 'queued'   -- the SKIP LOCKED claim query

itineraries (
  id uuid PK, request_id uuid REFERENCES trip_requests(id),
  version smallint, schema_version smallint NOT NULL,
  payload jsonb NOT NULL,                      -- the validated Itinerary object
  composer text NOT NULL,                      -- 'llm' | 'deterministic'  (fallback is visible)
  llm_provider text NULL, llm_model text NULL,
  candidate_set_hash char(64),                 -- reproducibility
  created_at timestamptz,
  UNIQUE (request_id, version)
)

itinerary_pois (itinerary_id uuid, poi_id uuid, day_number smallint, slot smallint)
```

`itinerary_pois` is not redundant with `payload`: it is the queryable audit trail proving every
rendered POI is a real row, and it powers "most-recommended places" analytics without parsing
jsonb.

### Phase 5 marketplace (designed, not built)

```sql
vendors  (id, name, region_id, contact, status, …)
products (id, vendor_id, name, price_paise, region_id, …)
vendor_tags  (vendor_id, tag_id)     -- same interest_tags
product_tags (product_id, tag_id)    -- same interest_tags
```

Tagged by district (`region_id`) and interest (`interest_tags`) exactly as required, reusing
both taxonomies unchanged.

---

## 3. Itinerary engine flow

One engine, five stages, run by the worker. Modes differ **only** in stage 1's filter.

```
TripRequest
   │
 [0] Normalise ──────────► TripBrief          (Pydantic; defaults, clamps, validation)
   │
 [1] Retrieve ───────────► CandidateSet       (pure SQL; caps + stable short IDs + hash)
   │
 [2] Compose (LLM) ──────► DraftItinerary     (forced tool_use, strict JSON schema)
   │
 [3] Validate / Repair ──► DraftItinerary     (1 repair round-trip → else deterministic)
   │
 [4] Route enrich ───────► RoutedItinerary    (travel_estimates, reorder, feasibility, return leg)
   │
 [5] Assemble + persist ─► Itinerary          (Pydantic-validated → itineraries.payload)
```

**Stage 1 — Retrieval.** Single SQL query over `pois` joined to `poi_tags`, `regions` and the
kind detail table: `status='published'`, region in scope, tag overlap, `cost_band <=`
budget, season-appropriate. Ranked by tag `weight` then `data_confidence`. Capped
(~30 places / 12 stays / 20 activities) to keep the prompt small and the model's choice space
tractable. Emits **stable short IDs** (`P1…Pn`, `S1…Sn`, `A1…An`) mapped to UUIDs, plus a
SHA-256 hash of the ID set for reproducibility.

**Stage 2 — LLM compose.** The prompt carries only the brief and the candidate list
(short ID, name, kind, one-line summary, duration, cost band, coarse locality) — no UUIDs, no
free-text place names to riff on. Output forced through a strict tool schema. Instructions:
select from the given IDs only; fill exactly N days; one stay per night; respect duration
budgets. The model's job is *taste and narrative* — theme, pacing, why this pairs with that —
not geography or arithmetic.

**Stage 3 — Validation and repair.** This is where "never invent places" is actually enforced:

- every referenced ID ∈ candidate set (**hard fail** — the core guarantee)
- day count == requested days; no gaps
- exactly one stay per night; a stay is not reused non-contiguously
- per-day activity duration within the daily budget
- no POI repeated across days unless flagged repeatable

One repair round-trip quoting the specific violations. If that fails, fall through to
`DeterministicComposer` (greedy: cluster candidates by locality, seed each day from the
highest-weight unused place, attach the nearest stay, fill with activities until the duration
budget is spent). `itineraries.composer` records which path ran, so silent degradation is
visible in the data rather than invisible in the UI.

The deterministic composer earns its keep twice: it is the fallback **and** the quality
baseline — "is the LLM actually beating greedy?" becomes a measurable question, the same way
`legal-rag`'s `chunks.strategy` column makes chunking strategies comparable.

**Stage 4 — Routing.** Fetch pairwise estimates from `travel_estimates` (Phase 1: haversine ×
1.35 road factor, ~28 km/h hill-road average, memoised on first use). Nearest-neighbour reorder
within each day from the day's anchor, keeping the LLM's day *grouping* but letting routing own
the *ordering* — the stated constraint. Then compute per-day travel totals, flag days over
`max_travel_minutes_per_day`, and append the return leg to `origin`.

**Stage 5 — Assemble.** Hydrate short IDs back to full POI records, validate against the
`Itinerary` Pydantic model, persist with `schema_version`, write `itinerary_pois`, mark the job
succeeded.

### Itinerary JSON schema (v1)

```jsonc
{
  "schema_version": 1,
  "itinerary_id": "uuid",
  "request_id": "uuid",
  "generated_at": "2026-08-10T12:00:00Z",
  "composer": "llm",                    // "llm" | "deterministic" — client may badge this
  "brief": {
    "mode": "interest",
    "interests": [{ "slug": "trekking", "label": "Trekking" }],
    "days": 3, "party_size": 4, "budget_band": 3,
    "origin": { "label": "Bengaluru", "lat": 12.9716, "lon": 77.5946 },
    "district": { "slug": "chikkamagaluru", "name": "Chikkamagaluru" }
  },
  "summary": {
    "title": "Three days of ridgelines and coffee country",
    "narrative": "…",                   // LLM prose; the only free-text field
    "total_distance_km": 412.5,
    "total_travel_minutes": 890,
    "estimated_cost": { "min_paise": 1800000, "max_paise": 3200000, "per_person": true },
    "warnings": [
      { "code": "long_travel_day", "day_number": 1, "message": "…" }
    ]
  },
  "days": [
    {
      "day_number": 1,
      "title": "Arrival and the first ridge",
      "narrative": "…",
      "travel": { "distance_km": 245.0, "duration_minutes": 330, "source": "static_haversine" },
      "stay": {
        "poi_id": "uuid", "name": "…", "stay_type": "homestay",
        "per_night": { "min_paise": 250000, "max_paise": 400000 },
        "lat": 13.31, "lon": 75.77, "contact": { … }, "media": [ … ]
      },
      "items": [
        {
          "slot": 1,
          "kind": "place",                       // "place" | "activity"
          "poi_id": "uuid",
          "name": "Mullayanagiri",
          "summary": "…",
          "why_chosen": "…",                     // LLM rationale, per item
          "start_time_estimate": "09:30",
          "duration_minutes": 120,
          "cost": { "min_paise": 0, "max_paise": 5000 },
          "lat": 13.39, "lon": 75.72,
          "media": [ … ],
          "leg_from_previous": {                 // null for the first item of a day
            "distance_km": 18.4, "duration_minutes": 45, "source": "static_haversine"
          },
          "guides": [ { "guide_id": "uuid", "name": "…", "languages": ["kn","en"], "contact": { … } } ]
        }
      ]
    }
  ],
  "return_leg": {
    "from_poi_id": "uuid",
    "to": { "label": "Bengaluru", "lat": 12.9716, "lon": 77.5946 },
    "distance_km": 245.0, "duration_minutes": 330, "source": "static_haversine"
  }
}
```

Two schema notes that matter for later phases: every leg carries its own `source`, so a
part-real / part-estimated itinerary is representable during the Phase 3 rollout; and money is
**integer paise** everywhere, never floats.

The LLM's tool schema is a strict *subset* of this — it returns only
`summary.title`, `summary.narrative`, and per day `title`, `narrative`, `stay` (short ID),
`items[{slot, kind, id, why_chosen}]`. Everything factual (coordinates, distances, costs,
contacts, times) is hydrated server-side from the DB. The model cannot emit a fact.

---

## 4. Repo structure

Separate repo at `~/aurasell-workspace/trip-planner`, git-init'd (the workspace root is not a
repo).

```
trip-planner/
├── CLAUDE.md                    # domain model + architectural decisions (below)
├── README.md
├── Makefile                     # single entry point; `make` prints help
├── docker-compose.yml           # db (default) + ollama (profile: llm)
├── .env.example
├── api/                         # FastAPI + engine + worker (one image, two entrypoints)
│   ├── pyproject.toml           # uv; extras: [llm], [dev]
│   ├── migrations/
│   │   └── 001_init.sql         # rationale in header comments
│   ├── seeds/
│   │   ├── regions.yaml
│   │   ├── interest_tags.yaml
│   │   └── chikkamagaluru/{places,stays,activities,guides}.yaml
│   ├── src/tripplan/
│   │   ├── config.py            # pydantic-settings, TRIPPLAN_ prefix
│   │   ├── db.py                # asyncpg pool
│   │   ├── cli.py               # typer: migrate, seed, plan, worker, db-info
│   │   ├── domain/
│   │   │   ├── models.py        # TripBrief, CandidateSet, DraftItinerary, Itinerary
│   │   │   └── taxonomy.py
│   │   ├── store/               # regions, pois, tags, requests, jobs, itineraries, travel
│   │   ├── engine/
│   │   │   ├── pipeline.py      # orchestrates stages 0-5
│   │   │   ├── retrieval.py     # stage 1  (mode-specific filters live here, only here)
│   │   │   ├── compose_llm.py   # stage 2
│   │   │   ├── validate.py      # stage 3  (the no-invention guarantee)
│   │   │   ├── compose_greedy.py# stage 3 fallback + baseline
│   │   │   ├── routing.py       # stage 4
│   │   │   └── assemble.py      # stage 5
│   │   ├── llm/                 # port + adapters (hosted, ollama, claude)
│   │   ├── routing/             # RoutingProvider port + StaticEstimateProvider
│   │   ├── jobs/worker.py       # SKIP LOCKED claim loop
│   │   ├── api/                 # routers: taxonomy, plan, itinerary, health
│   │   └── observability/
│   └── tests/                   # unit + integration (self-skip without DB)
└── web/                         # Next.js 15
    ├── app/plan/                # wizard: interests → days/people/budget → review
    ├── app/itinerary/[id]/      # polling status view → card render
    ├── components/itinerary/    # DayCard, StayCard, ItemCard, TravelLeg, WarningBanner
    └── lib/api.ts + types.ts    # types generated from the OpenAPI schema
```

### Initial `CLAUDE.md` contents

1. **What this is** — one paragraph, plus the Phase 1 scope boundary.
2. **Domain model** — the vocabulary, stated once so it is used consistently: *POI* (supertype
   of place/stay/activity), *interest tag*, *region* (state→district→taluk→locality with
   materialised `path`), *trip request*, *brief*, *candidate set*, *draft* vs *routed* vs
   *final* itinerary, *composer*, *leg*.
3. **The four hard rules** — (a) the LLM selects from candidate IDs only, enforced in
   `engine/validate.py`; (b) distances/ordering come from a `RoutingProvider`, never the model;
   (c) generation is an async job, the HTTP endpoint only enqueues; (d) the itinerary is a
   versioned Pydantic schema, `schema_version` bumps on any breaking change.
4. **Decisions and their reasons** — POI supertype over three tables; one `interest_tags`
   vocabulary partitioned by `kind` (so Phase 5 adds rows, not tables); `source` in
   `travel_estimates`' PK (so real and estimated ETAs coexist); Postgres job queue over
   Celery; lat/lon + haversine now, PostGIS later; money as integer paise;
   `itineraries.composer` recording LLM-vs-fallback.
5. **Where things go** — mode-specific filtering belongs in `engine/retrieval.py` and nowhere
   else; provider-specific code stays behind the `llm/` and `routing/` ports.
6. **Conventions** — migrations are frozen once applied; `make check` before commit; new POI
   rows require `source` + `data_confidence`.
7. **Phase map** — what is built vs designed-for, so future work doesn't re-litigate.

---

## 5. Phase 1 build sequence

Eight reviewable steps. Each ends at a point where you can run something and judge it.

| # | Step | Deliverable / review gate |
|---|---|---|
| **1** | **Skeleton + infra.** Repo, git init, `pyproject.toml`, `Makefile`, `docker-compose.yml` (`tripplan-db` on 5434), `.env.example`, config, structlog, typer CLI, migration runner, `make check` green on a smoke test. | `make install && make up && make db-info` reports Postgres 17 + pgvector. Nothing domain-specific yet — review the conventions. |
| **2** | **Schema.** `001_init.sql` with the full section-2 schema, rationale in header comments, haversine SQL function, indexes. `make migrate`. | Review the DDL. This is the step to argue with — migrations freeze once applied. |
| **3** | **Taxonomy + region seed.** `interest_tags` (Phase 1 interests) and `regions` for Karnataka → Chikkamagaluru → its taluks. Idempotent YAML loader. | `make seed-taxonomy`; review the tag list and the `path` values, since every later filter depends on them. |
| **4** | **POI seed set.** ~40–60 hand-compiled records for Chikkamagaluru across places/stays/activities + a few guides, each with `source`, `data_confidence`, `status='draft'`. Loader validates and reports gaps (missing coords, untagged POIs). | **Your fact-check pass.** You correct the YAML; only reviewed rows get promoted to `status='published'`. Retrieval sees nothing else. |
| **5** | **Retrieval + deterministic composer + routing (no LLM).** Stages 0, 1, 3-fallback, 4, 5. `tripplan plan --interests trekking,spiritual --days 3 --people 4 --budget 3` prints a complete valid itinerary. | A full end-to-end itinerary with **zero** LLM involvement. This proves the data, schema and routing are sound before the model is in the picture — and it is the permanent quality baseline. |
| **6** | **LLM compose + validation/repair.** `llm/` port and adapters, strict tool schema, stage 2, stage 3 validation with one repair round-trip and fallback. Tests that a hallucinated ID is rejected and that a fabricated-ID response falls back rather than surfacing. | Same CLI command, now `composer: "llm"`. Diff it against step 5's output — that diff *is* the model's contribution. |
| **7** | **API + worker.** `POST /api/plan` (enqueue → 202 + ids), `GET /api/itineraries/{id}` (status or payload), `GET /api/taxonomy/interests`, `/health`. Worker with SKIP LOCKED claim, per-stage progress, retries with backoff, poison-job handling. | `curl` the enqueue, poll to completion. Kill the worker mid-job and confirm another claims it after the lock expires. |
| **8** | **Frontend.** Wizard (interests → days/people/budget → origin), polling status screen, card-based itinerary render (day cards, stay card, item cards with `why_chosen`, travel legs, warnings, return leg), guide details where present. Types generated from OpenAPI. | Click through the whole flow in the browser. |

**Not in Phase 1** (kept out deliberately): auth, the other two planning modes, real maps or an
interactive map, bookings, marketplace, itinerary editing, sharing, i18n. The schema
accommodates each; none is built.

---

## Verification

**Per-step, automated:**
- `make check` (ruff + `mypy --strict` + pytest) is the gate for every step.
- Integration tests self-skip when `tripplan-db` is down, so unit tests stay runnable offline.
- Schema invariant test: every `poi_tags.tag_id` resolves; every published POI has coordinates.

**The guarantees that matter, each with a dedicated test:**
1. *No invented places* — feed `compose_llm` a stubbed response containing IDs outside the
   candidate set; assert stage 3 rejects it, and that after a failed repair the result is
   `composer='deterministic'`, never a hallucinated POI.
2. *Model owns no facts* — assert every coordinate, distance, duration, cost and contact in a
   generated payload matches its DB row exactly.
3. *Routing owns ordering* — assert intra-day ordering matches nearest-neighbour output, not
   the model's emitted `slot` order.
4. *Schema stability* — validate persisted payloads against the `Itinerary` model; a breaking
   change must bump `schema_version` or the test fails.
5. *Job durability* — kill the worker mid-job; assert the lock expires and another worker
   completes it exactly once.

**End-to-end manual:**
- CLI: `make plan INTERESTS=trekking,spiritual DAYS=3 PEOPLE=4 BUDGET=3` — run once with the
  deterministic composer and once with the LLM, and read both. If the LLM version isn't
  visibly better, that is a finding worth acting on.
- API: enqueue via `curl`, poll to `succeeded`, diff the payload against the CLI output.
- Web: complete the wizard in a browser and confirm the cards render every schema field,
  including warnings and the return leg.
- Data honesty: `SELECT status, count(*) FROM pois GROUP BY status` — unreviewed rows must stay
  `draft` and must not appear in any itinerary.
