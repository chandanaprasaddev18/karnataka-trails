# Hosting this on free tiers

Three services, three free accounts, no card required by any of them:

| Piece | Host | Why this one |
|---|---|---|
| Postgres | **Neon** | Free tier does not expire and ships `pgvector`, which migration 001 requires. Render's free Postgres expires; Supabase works too. |
| API + job worker | **Render** (Docker) | The process must stay alive to drain the job queue. Serverless cannot hold a polling loop. |
| Frontend | **Vercel** | Next.js 16 with server components, which is what Vercel is for. |

Everything below was verified locally first: the image builds, runs the worker
in-process, applies migrations on boot, plans a real itinerary with OSRM routing,
and honours `TRIPPLAN_CORS_ORIGINS`.

## What a free tier actually gives you

State these to anyone you share the link with, because they are visible:

- **The API sleeps after ~15 minutes idle.** The next request pays 30–50 seconds of
  cold start. The first page load after a nap looks broken; it is not.
- **While asleep nothing drains the queue.** A plan enqueued as the instance stops
  waits for the next visitor to wake it. The itinerary page polls, so it recovers on
  its own — it just looks slow.
- **512 MB RAM, shared CPU.** Composition takes a few seconds rather than
  milliseconds, and OSRM adds a round trip per plan.
- **Photographs come from Wikimedia's CDN in production** (`NEXT_PUBLIC_PHOTO_SOURCE=remote`),
  because the 73 MB of downloaded files are gitignored. If Wikimedia throttles a
  viewer they see the gradient fallback instead of a photograph. Object storage is
  the fix when this stops being a prototype.
- **Routing uses the public OSRM demo server**, which asks for light use and offers
  no availability guarantee. Fine for a few testers; point
  `TRIPPLAN_ROUTING__OSRM_BASE_URL` at your own instance for anything more.

## 1. Database — Neon

1. https://neon.tech → sign in with GitHub → **New project**, region closest to your
   testers (`ap-southeast-1` for India).
2. Copy the **direct** connection string — the one WITHOUT `-pooler` in the host:
   `postgresql://user:pass@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`

   Why not the pooled one: asyncpg prepares and caches statements per connection,
   and a transaction-mode pooler hands the same server connection to different
   clients between transactions, so you get intermittent
   `prepared statement "__asyncpg_stmt_1__" does not exist` under load after
   everything looked fine in testing. The app detects a pooler URL and disables the
   statement cache anyway (`db.py::_statement_cache_size`), so the pooled string
   will work — the direct one is simply faster, and one instance with a 10-connection
   pool has no need for external pooling.
3. Load the schema and data from your machine — the seed files live here, not on the
   server, and publishing should be a deliberate act rather than something a web
   process does on boot:

   ```bash
   cd trip-planner
   export TRIPPLAN_DB__URL='<neon DIRECT connection string>'
   make migrate                       # 8 migrations
   uv --project api run tripplan seed-taxonomy
   uv --project api run tripplan seed-pois          # all six districts
   uv --project api run tripplan publish            # promotes what passes the gate
   uv --project api run tripplan fetch-photos       # populates media[] (~10 min)
   unset TRIPPLAN_DB__URL
   ```

   `fetch-photos` is what fills the `media` column, and the production frontend
   reads the Commons URL out of it. Skip it and the site has no photographs.

## 2. API — Render

1. https://render.com → sign in with GitHub → **New** → **Blueprint** → pick
   `chandanaprasaddev18/karnataka-trails`. It reads `render.yaml`.
2. Set the two secrets it asks for (both marked `sync: false`, so they never enter
   git):
   - `TRIPPLAN_DB__URL` — the Neon string from step 1
   - `TRIPPLAN_CORS_ORIGINS` — your Vercel URL, e.g. `https://karnataka-trails.vercel.app`.
     No wildcard: these endpoints echo a session token.
3. Deploy. Watch for `api.migrated`, `api.worker_in_process` and `api.started` in the
   logs, then check `https://<your-api>.onrender.com/health` — it must report
   `"database": true` and a non-zero `published_pois`.

You will not know the Vercel URL until step 3, so set `TRIPPLAN_CORS_ORIGINS`
afterwards and redeploy; until then the browser blocks the calls.

## 3. Frontend — Vercel

1. https://vercel.com → **Add New Project** → import the same repo.
2. **Root Directory: `web`** — the repo root is not the Next app, and Vercel will
   otherwise build nothing.
3. Environment variables:
   - `NEXT_PUBLIC_API_BASE` = `https://<your-api>.onrender.com`
   - `NEXT_PUBLIC_PHOTO_SOURCE` = `remote`
4. Deploy, then go back and set `TRIPPLAN_CORS_ORIGINS` on Render to the Vercel URL.

## 4. Check it actually works

```bash
curl -s https://<your-api>.onrender.com/health
```

Then in the browser: home page shows six districts with photographs → plan by
district → Mysuru → the itinerary appears with `measured on real roads` under the
driving time. The first attempt after idle is slow; that is the cold start.

## If something is wrong

| Symptom | Cause |
|---|---|
| Home page says "Could not reach the API" | `NEXT_PUBLIC_API_BASE` wrong, or the API is asleep — retry once. |
| Districts load but planning hangs at "Waiting for a worker" | `TRIPPLAN_WORKER__IN_PROCESS` is not `true`, so nothing claims jobs. |
| Browser console shows CORS errors | `TRIPPLAN_CORS_ORIGINS` does not exactly match the Vercel origin (scheme included, no trailing slash). |
| No photographs anywhere | `fetch-photos` never ran against the hosted database, so `media` is empty. |
| `/health` says `published_pois: 0` | `publish` never ran; retrieval only ever selects published rows. |
| Everything works but driving times look invented | `TRIPPLAN_ROUTING__PROVIDER` is not `osrm`, so legs fall back to straight-line estimates (each leg says `estimated` in the UI). |
