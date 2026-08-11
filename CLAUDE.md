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
- **The seasonal filter is a hard exclusion, not a ranking signal.** Kudremukh in
  peak monsoon is a safety question, so an out-of-season POI is removed rather
  than down-ranked. What used to be wrong was the *failure mode*, not the filter:
  see `store/pois.py::feasibility`.
- **Feasibility is checked at POST time, and again in the engine.** The API
  refuses an impossible brief immediately with alternatives; the engine keeps its
  own guard for data that changes between accept and run. Both use the same
  diagnosis so they cannot disagree.
- **Published ≠ verified.** `publish` promotes rows but leaves `verified_at`
  NULL, so "published but never checked" stays queryable, and the itinerary
  raises an `unverified_data` warning while it is true.

## Where things go

| Change | Goes in |
|---|---|
| A planning mode's filter | `store/pois.py` — and nowhere else |
| A frontend type | `web/lib/types.ts`, mirroring `domain/models.py` |
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
- **Stop the worker before running the integration suite.** The tests share the
  dev database, so a running worker claims the jobs they enqueue and the queue
  tests fail with a genuine-looking SKIP LOCKED assertion. The failure is the
  environment, not the code.
- New POI rows require `source` and `data_confidence`. Guides must not carry
  invented names or contact details — a test enforces this.

## Photographs

`store/photos.py` sources Creative Commons images from Wikimedia Commons via
`tripplan fetch-photos`. Four rules, all of them there for a reason that bit:

1. **The file TITLE must name the place.** Matching on the description or
   categories was tried and attached wrong images — a description mentioning
   "Chikkamagaluru district" is not evidence the photograph shows the place asked
   about.
2. **Only places.** Stays are private properties with no Commons photo, and an
   activity's name describes an action: "Birding morning at Bhadra" matched
   "Cormorant in the early morning sun" and put a Bangalore photo on a Bhadra
   activity. Both fall back to their locality's image in the UI.
3. **Reject contradicting locations.** Karnataka shares names with its
   neighbours; "Kalhatty Falls ooty.jpg" is a Tamil Nadu waterfall with the same
   name as ours. `_CONTRADICTING_PLACES` catches that class.
4. **No readable licence, no photo.** Attribution is a licence condition, so
   `artist`, `license` and `source_page` are stored and *rendered* on every image.

Images are **downloaded**, not hotlinked: Commons returned 429 under mild bursts
through Next's image optimiser, and a page load should not depend on a free
external service. `web/public/photos/` is generated and gitignored — repopulate
with `make fetch-photos`.

Coverage today: **22 of 29 published places** carry photographs (three each, 60
in total), plus 8 regions — 76 files, 26 MB. Stays and activities have none by
design and borrow their locality's, labelled as the area. The remaining gaps are
places nobody has photographed on Commons; the UI renders a generated gradient
rather than a stand-in image.

Two matching rules were added after a place with dozens of Commons photographs
came back empty:

- **Search both `"<name> <region>"` and the bare `"<name>"`, and union the
  results.** The context-qualified query alone returned district gazetteer PDFs
  for Baba Budangiri, and because the fallback only ran when a query returned
  *nothing*, those junk hits blocked every good photograph.
- **A taluk with no photograph of its own may borrow one from a published place
  inside it.** Locality-level fallbacks are what stay cards and unphotographed
  stops render, so an empty taluk left whole days with no imagery.

## Design language

Follows `trip-planner-web-mockups.html`: deep navy paired with cream, Fraunces
for display, IBM Plex Mono for eyebrows, passport-stamp interest cards, a dotted
timeline.

Where it deliberately diverges: the mockup uses four accents (orange, pink, teal,
yellow). With real photographs on the page every card already carries its own
colour, so four competing accents made the layout noisy and — the thing that
actually mattered — stopped the warning states reading as warnings. An itinerary
here routinely carries five or six advisories and those must be the loudest
element. Cut to three, each with one job: **marigold** for primary action and
selection, **terracotta** for the timeline and anything cautionary, **teal** for
quiet affirmative detail. Nothing else gets a colour.

## Frontend notes

- **Next 16.** `next dev` writes `web/AGENTS.md` (and a `CLAUDE.md` that includes
  it) warning that this version has breaking changes. Read
  `web/node_modules/next/dist/docs/` before writing frontend code; the one that
  bites here is that `params` is a Promise. Use the generated
  `PageProps<'/route'>` helper (`npx next typegen`, or `make web-check`).
- **`web/lib/types.ts` is hand-written**, not generated from the OpenAPI schema —
  codegen would need the API running at build time. `schema_version` is the
  safety net: the renderer refuses a payload version it does not understand
  rather than silently dropping fields.
- **No arithmetic in the browser.** Totals, costs and times are computed
  server-side; the client divides paise by 100 for display only. A number on
  screen cannot disagree with the number that was stored.
- `make web-check` type-checks the frontend; `make check-all` runs both sides.
- **`PhotoFrame` takes an explicit `variant`, not overriding classes.** Tailwind
  emits `relative` after `absolute` in its own canonical order, so a caller's
  `absolute inset-0` silently loses to the component's `relative`, the wrapper
  collapses to zero height, and the image renders nothing. Use
  `variant="cover"` for a background and `variant="sized"` for a thumbnail.
- **Testing a mobile layout needs CDP device emulation.** Headless Chrome's
  `--window-size` does not set the layout viewport, so a narrow screenshot just
  crops a desktop-width render and looks like an overflow bug that is not there.
- **Never put a `PhotoFrame` with its credit inside a `Link`.** The credit is an
  anchor to the Commons source page, and a nested `<a>` is invalid HTML that
  fails hydration — React regenerates the tree and the console fills with errors.
  Pass `showCredit={false}` and render the attribution as text alongside.
- **A screenshot does not trigger lazy loading.** `Page.captureScreenshot` with
  `captureBeyondViewport` renders the whole page, but `next/image` waits on an
  IntersectionObserver, so everything below the fold photographs as an empty box.
  Scroll the page first, or you will go hunting for a rendering bug that is a
  camera artefact. (Same family as the `--window-size` trap above.)
- **`img.naturalWidth` is density-corrected.** With a `w`-descriptor `srcset`,
  the browser divides the intrinsic width by the selected candidate's effective
  density — a 1280px file in a 1280px box reports 426. It is not a low-resolution
  image; check the file, not the DOM.

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
- **The LLM success path is unverified against a live provider** — no API key is
  available on this machine. Everything up to and including the HTTP call is
  tested, and the degradation path was verified live (401 -> fallback).
- **Frontend types are hand-written** (see Frontend notes). If they drift, the
  `schema_version` check catches a breaking change but not an added field.

## Phase map

| Phase | Status |
|---|---|
| 1 — Plan by Interest, Chikkamagaluru, cards | in progress |
| 2 — Plan by Location / District | designed for; reuse `store/pois.py` |
| 3 — Real maps routing + map view | designed for; `RoutingProvider` + `travel_estimates.source` |
| 4 — Stay and guide booking | designed for; tables sit beside `guides` |
| 5 — Marketplace | designed for; reuses `interest_tags` + `region_id` |
