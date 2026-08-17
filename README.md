# Trip Planner

Day-by-day trip itineraries for Karnataka, composed by an LLM over a **curated
database** and routed by a real routing provider — never by the model.

Full architecture, schema rationale, engine flow, JSON schema and build sequence:
**[`docs/architecture.md`](docs/architecture.md)**. Working agreements and the
domain vocabulary: [`CLAUDE.md`](CLAUDE.md).

## Quick start

```bash
make env         # create .env from .env.example
make install     # uv sync (Python 3.12)
make up          # Postgres 17 + pgvector on :5434
make migrate     # apply migrations
make seed        # load taxonomy, regions, POIs, vendors, specialities (draft)
make publish     # promote fact-checked POIs to status=published
make fetch-photos # download CC photos from Wikimedia Commons (optional)
make plan        # generate an itinerary on the CLI
```

`make` with no target lists every command.

## Layout

| Path | What |
|---|---|
| `api/` | FastAPI app, itinerary engine, worker, CLI (one image, two entrypoints) |
| `api/migrations/` | Forward-only, checksummed SQL migrations |
| `api/seeds/` | Curated YAML seed data + loader input |
| `api/tools/` | One-shot importers. `import_directory.py` turns a district/place spreadsheet into seed YAML with geocoded coordinates |
| `web/` | Next.js frontend |
| `web/public/photos/` | Downloaded photographs — generated, gitignored |
| `docs/architecture.md` | The design plan this build follows |

## Photographs

`make fetch-photos` sources images from Wikimedia Commons and saves them locally.
Three rules keep it honest:

- A photo is attached only when the Commons **file title names the place**. A
  wrong photo misinforms a traveller, so an unmatched place simply gets a
  generated gradient instead.
- Only *places* get photographs. A stay is a private property with no Commons
  image, and an activity's name describes an action rather than a location — that
  is how "Birding morning at Bhadra" once matched a cormorant photo from
  Bangalore. Both fall back to their locality's image.
- **No readable licence, no photo.** Author and licence are stored and rendered on
  every image, because attribution is a condition of the licence.

22 of 29 published places get photographs (three each), plus 8 regions — 76 files,
26 MB. Stays and activities have none of their own and show their locality's,
labelled as such. That ratio is the point: the gaps are honest.

## The four rules

1. The LLM selects from a retrieved candidate set only — it cannot name a place
   that isn't in our database. Enforced in `engine/validate.py`, not the prompt.
2. Distances, durations and stop ordering come from a `RoutingProvider`.
3. Generation is an async job; the HTTP endpoint only enqueues.
4. The itinerary is a versioned Pydantic schema (`schema_version`).

## Current status

**Data** — six districts seeded: Chikkamagaluru hand-compiled (60 POIs: places,
stays, activities, guides), plus Bengaluru Urban, Mysuru, Uttara Kannada,
Vijayanagara (Hampi) and Kodagu imported from a tourism spreadsheet with
coordinates geocoded against OpenStreetMap. The imported districts have places
only — no stays — which itineraries there report rather than paper over. See
"Seed data" in `CLAUDE.md` before trusting an imported row.

**Phase 1** — Plan by Interest, card output, no auth. Done.

**Phase 2** — Plan by Location (radius around any place or town we hold) and Plan
by District (the whole district, interests optional). Done, front to back. All
three modes are one engine differing in a single WHERE clause; see
`_scope_for` in `api/src/tripplan/store/pois.py`.

**Phase 3** — Real road distances from OSRM, with the static estimator kept as a
labelled fallback, plus a route map drawn from the returned road geometry. Done.
An interactive basemap is not built: tiles would put a third-party request on
every page view.

**Phase 4** — Booking, built as **requests**. The app records what you want and
says plainly that it cannot send it: no stay in our data has a verified contact,
every guide is a placeholder, and there is no partner API or payment provider. The
database refuses to mark anything `sent` or `confirmed` without a real channel.

**Phase 5** — Marketplace, built **seller-free**. It lists what each district
produces (tagged by region and by category, reusing the planner's own taxonomy) and
surfaces it on the itinerary as "take home". It lists no vendors: every vendor
record we hold is a development placeholder that the publish gate refuses, because
a vendor is somebody a traveller might try to pay. The page states that plainly
instead of looking like data that failed to load.

See the phase map in `CLAUDE.md`.
