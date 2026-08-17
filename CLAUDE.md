# CLAUDE.md — working agreements for this repo

Read this before changing anything. The full design rationale lives in
[`docs/architecture.md`](docs/architecture.md); this file is the short version
plus the rules that are easy to break by accident.

## What this is

A trip planner for Karnataka. A user picks how to start (by Interest, by
Location, by District), answers three questions (days, people, budget), and gets
a day-by-day itinerary: route from their origin, stays, activities, places,
guides where available, and a return leg.

**Where it is now:** all three planning modes work; driving is measured on the real
road network (OSRM) and drawn from the returned geometry; bookings exist as
**requests** and the marketplace exists **without sellers** — see the section on
what those two deliberately do not do. Six districts are seeded (66 places and
activities), and there are no accounts, so "my requests" is scoped to a browser
session.

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
- **"Enough to plan" counts DAYS, not just candidates.** One stop per day minimum,
  so a five-place district refuses an eight-day brief immediately and offers the
  longest trip that fits. Without the day count the brief was accepted and the job
  failed half a minute later complaining about empty days.
- **The greedy composer shares the pool across remaining days** instead of filling
  each day to its time budget. Front-loading was invisible while one district had
  forty places; with five it put all of them on day 1 and produced two fatal empty
  days.
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
- **Never upsert a row whose id comes from a smallint sequence.** `INSERT ... ON
  CONFLICT DO UPDATE` evaluates `nextval()` before it detects the conflict, so a
  re-seed burns an id per row even when nothing changes. `interest_tags.id` is a
  smallint (2 bytes, because `trip_requests.tag_ids` snapshots it), the tests
  re-seed the taxonomy for every test, and the sequence hit its 32767 ceiling on a
  table with 30 rows. Fixed in migration 008 plus an update-then-insert loader.
- **Stop the worker before running the integration suite.** The tests share the
  dev database, so a running worker claims the jobs they enqueue and the queue
  tests fail with a genuine-looking SKIP LOCKED assertion. The failure is the
  environment, not the code.
- New POI rows require `source` and `data_confidence`. Guides must not carry
  invented names or contact details — a test enforces this.

## Seed data: two provenances, and they are not equivalent

**Chikkamagaluru is hand-compiled.** Someone typed every coordinate, duration and
fee, and can be asked why. Confidence 2-3.

**The other five districts were imported** from a tourism spreadsheet by
`api/tools/import_directory.py`, with coordinates geocoded against OpenStreetMap.
Read that file's docstring before touching them. What matters here:

- **Confidence is capped at 2** and `verified_at` stays NULL, so every itinerary
  containing them raises `unverified_data`. Only a person checking a place earns 3.
- **Every row cites the OSM object it matched** (`source_url`) and records that
  object's display name in `place_details.notes`. Check the notes line first — it is
  how a wrong match is spotted. One is already visible: Jog Falls' OSM match says
  *Shimoga district* while the spreadsheet files it under Uttara Kannada.
- **`api/seeds/geocode_aliases.yaml` is data, not config.** A tourism name and a map
  name differ ("Dandeli Wildlife Sanctuary" is the town Dandeli plus the Kali Tiger
  Reserve), and three aliases are outright corrections: without them the geocoder
  returned a HIGHWAY for Jog Falls, the High Court for Bengaluru Palace, and a
  Mysuru neighbourhood for Vijayanagara district. Each alias records where it landed
  when reviewed, and the importer warns if it drifts.
- **A place that cannot be placed is skipped and named**, never given an approximate
  coordinate. Lalbagh Botanical Garden is the current casualty: OSM has no object
  for the garden, only a metro station 600 m away.
- `api/tests/test_imported_places.py` is the standing version of the checks that
  caught those errors — inside Karnataka, citation present, confidence capped, no
  two places within 50 m, district centroid agreeing with its own places.

Imported districts have **places only**: no stays, no activities, no guides. That is
reported by `make seed` per district and shows up on itineraries as
`no_stay_available`, which is the honest outcome rather than an invented homestay.

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

Coverage today: **46 of 53 published places** carry photographs, plus 14 regions. Stays and activities have none by
design and borrow their locality's, labelled as the area. The remaining gaps are
places nobody has photographed on Commons; the UI renders a generated gradient
rather than a stand-in image.

Adding five districts broke four things in this fetcher, all of them the
single-district era showing through:

- **The district was hardcoded.** The region branch searched
  `region_name="Chikkamagaluru Karnataka"` for every district, so five of six
  searched under the wrong name — and the contradiction check then rejected a
  correct photo of Hampi for "being in Hampi, not here".
- **The contradiction list is now district-aware** (`_DISTRICT_LOCALITIES`). Hampi,
  Mysore, Coorg and Gokarna were on the "somewhere else" list; four of them are
  now places we hold.
- **A category check was needed.** "Vijayanagara Empire c.1485.png" — a historical
  map — became a district hero image: the title filter looks for the word "map" and
  that title has none. Files are now rejected by their Commons CATEGORIES.
  Note `cllimit=max`: the limit is shared across all titles in one request, so a
  small value silently returns categories for the first few files and none for the
  rest, which is exactly how the map slipped through the first fix.
- **`name_tokens` needed three-letter words.** "Jog Falls" had no distinctive token
  at all ("jog" is three characters, "falls" is a stopword), making one of the
  state's best-known waterfalls unmatchable.

Two matching rules were added earlier, after a place with dozens of Commons
photographs came back empty:

- **Search both `"<name> <region>"` and the bare `"<name>"`, and union the
  results.** The context-qualified query alone returned district gazetteer PDFs
  for Baba Budangiri, and because the fallback only ran when a query returned
  *nothing*, those junk hits blocked every good photograph.
- **A taluk with no photograph of its own may borrow one from a published place
  inside it.** Locality-level fallbacks are what stay cards and unphotographed
  stops render, so an empty taluk left whole days with no imagery.

## Design language

Dark navy ground with a single gold accent, following the Wanderly reference the
owner supplied (`~/Downloads/ChatGPT Image Aug 13, 2026, 01_52_18 PM.png`): a left
nav rail listing the whole product, panels lifted out of a near-black ground with
hairline borders, and a right rail on the itinerary holding the route map and the
day's timeline.

Tokens live in `web/app/globals.css`: `ink-950..ink-700` for ground and panels,
`gold` for the accent, `rust` for caution, `teal` for quiet affirmatives. Use the
`.panel` class rather than re-declaring background + border + radius per card.

Two rules that override the reference, both about honesty:

1. **No invented data in the chrome.** The reference puts a star rating
   ("4.8 (1.2K)") and a price ("From ₹4,999") on every card, and a "Confirmed"
   booking chip on the trip. We have no reviews, no inventory and no bookings, so
   those slots carry facts we do hold: published place count, which months are
   open, whether a permit is needed, whether the driving was measured. A
   fabricated 4.8 would be the most damaging thing on the page.
2. **Gold means action or selection, nothing else.** An itinerary routinely
   carries five or six advisories and those must be the loudest thing on screen,
   so caution keeps its own rust. Decorative gold would flatten that hierarchy.

Sidebar items for unbuilt features (My trips, Saved, Bookings, Guides,
Marketplace, Experiences, Hidden gems) render as text with a "soon" marker and the
reason in the title attribute. A visible roadmap beats a link that 404s or, worse,
one that silently does nothing.

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
- **A cross-origin response header is invisible to JS unless the server exposes
  it.** The API mints a session token for a first-time booking and returns it in
  `X-Session-Token`; without `expose_headers` on the CORS middleware the browser
  could not read it, so the request was stored under an identity the client never
  learned and "my requests" came back empty. The client now also mints its own
  token when it has none, so the feature does not depend on that header at all.
- **`make web-check` runs ESLint with `react-hooks/exhaustive-deps` as an ERROR.**
  It is there because a missing dependency shipped a wrong feature: the plan
  wizard's submit callback omitted `district` from its deps, so it captured the
  value the page loaded with. The user picked Mysuru, the card showed as selected,
  the request said Chikkamagaluru — invisible to TypeScript, invisible in the DOM,
  and only detectable by reading the resulting itinerary.
- **A grid child needs `min-w-0`.** Grid items default to `min-width: auto`, so
  the widest unbreakable descendant sets the column width and pushes the page
  past the viewport. This produced a 9px horizontal overflow on the itinerary at
  390px that no amount of `max-w` on the children could fix.
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

## Bookings and the marketplace: what they deliberately do not do

Phases 4 and 5 exist, and both stop short of the thing their names imply. That is a
data judgement, not an unfinished feature, and changing it needs new data rather
than new code.

**A booking is a REQUEST.** No stay in the seed set has a verified contact
(`stays.yaml` leaves it empty on purpose), every guide is a placeholder, and there
is no partner API or payment provider. So `POST /api/bookings` records intent as
`requested` with `sent_via = NULL`, and every surface says plainly that we could
not deliver it. The status vocabulary names who is waiting on whom
(`requested → sent → confirmed | declined | withdrawn`), and
`bookings_sent_needs_channel` makes the database refuse `sent` or later without a
real channel. **Only a partner integration may ever write `confirmed`.**

**The marketplace lists places, not sellers.** `region_specialities` — what a
district produces, keyed to a `product_category` tag — has data and is genuinely
useful ("Take home" on an itinerary reads from it). `vendors` and `products` are
built, tagged and tested, and every seeded row is a placeholder the publish gate
refuses, because a vendor is somebody a traveller might try to PAY. The page states
the number of sellers listed (zero) and how many vendor records are withheld,
rather than looking like data that failed to load.

The corollary for anyone extending this: adding a real stay contact or a real
vendor is a seed-file change plus a review. If you find yourself writing a
plausible phone number to make a screen look finished, stop.

## Known gaps and follow-ups

- **Re-seeding preserves `status`.** Editing a fact on an already-published row
  does not force re-verification. A `seed_hash` column plus a demote-on-change
  rule would fix it; deliberately deferred.
- **Distances are real; the static estimator remains as a fallback.** Any pair
  OSRM cannot answer falls back and is labelled `static_haversine`, so a partly
  degraded plan says which legs were guessed. The public OSRM demo has no
  availability guarantee — point `TRIPPLAN_ROUTING__OSRM_BASE_URL` at your own
  instance for anything real.
- **Scheduling ignores opening hours.** `place_details.opening_hours` is stored
  but unused; the engine only warns via `late_finish`.
- **All seeded guides, vendors and products are synthetic placeholders**, so those
  paths are only exercised with `tripplan publish --include-placeholders` (local
  dev only). 18 rows are refused by the gate and each is named in its report.
- **Bookings are scoped by browser session**, not by account, so a traveller's
  requests do not follow them to another device. The page says so. Accounts would
  populate `bookings.user_id`, which already exists.
- **The LLM success path is unverified against a live provider** — no API key is
  available on this machine. Everything up to and including the HTTP call is
  tested, and the degradation path was verified live (401 -> fallback).
- **Frontend types are hand-written** (see Frontend notes). If they drift, the
  `schema_version` check catches a breaking change but not an added field.

## Phase map

| Phase | Status |
|---|---|
| 1 — Plan by Interest, Chikkamagaluru, cards | **built** |
| 2 — Plan by Location / District | **built** — `_scope_for` in `store/pois.py` |
| 3 — Real maps routing + map view | **built** — OSRM provider + SVG route map. No basemap. |
| 4 — Stay and guide booking | **built as REQUESTS.** We cannot confirm a booking — see below |
| 5 — Marketplace | **built, seller-free.** District specialities today; vendors gated |
