-- 001_init.sql — initial schema.
--
-- Forward-only migrations, applied in filename order and checksummed. Same
-- contract Flyway gives you in the Spring world: once a migration has run its
-- content is frozen. Change it and the runner refuses to start.
--
-- DESIGN NOTES (the reasoning matters more than the DDL):
--
-- 1. `pois` is a SUPERTYPE table with a `kind` discriminator, not three
--    parallel tables for places/stays/activities. Those three share ~15
--    columns (name, geo, region, description, duration, cost, media,
--    provenance) and all three need tagging. Three tables would mean three tag
--    join tables, three retrieval queries and three code paths in the engine.
--    One supertype gives the engine a single uniform candidate query and the
--    taxonomy a single join table with real foreign keys.
--    Java analogue: class-table inheritance, @Inheritance(strategy = JOINED).
--
-- 2. `interest_tags.kind` partitions ONE tag vocabulary. Phase 1 seeds
--    kind='interest'. Phase 5's marketplace adds kind='product_category' ROWS
--    to this same table and tags vendors/products against it. That is the
--    "reuse the taxonomy without a rewrite" requirement, and it is why `kind`
--    exists on day one rather than being retrofitted.
--
-- 3. `regions.path` is a materialised ancestry string
--    ('/karnataka/chikkamagaluru/mudigere/'). A LIKE prefix match answers
--    "every POI in this district including its taluks" without a recursive
--    CTE. This single column is what makes Phase 2's three planning modes the
--    same query with a different WHERE clause.
--
-- 4. `travel_estimates` puts `source` IN THE PRIMARY KEY. Phase 3's maps API
--    writes real ETAs ALONGSIDE Phase 1's haversine placeholders instead of
--    overwriting them, so both datasets coexist, real-vs-estimated routes stay
--    directly comparable, and the cutover is a config change rather than a
--    destructive backfill.
--
-- 5. Money is integer paise everywhere (bigint), never float. Rounding drift in
--    a price range shown to a user is a bug, and 1 INR = 100 paise exactly.
--
-- 6. `itineraries.composer` records whether the LLM or the deterministic
--    fallback produced a given itinerary. Without it, a degraded LLM path is
--    invisible in production; with it, "how often are we falling back?" is a
--    SQL query.
--
-- 7. `trip_requests.user_id` is nullable from the start. Phase 1 has no auth
--    and identifies owners by `session_token`; adding auth later populates the
--    column instead of migrating the itinerary tables.

CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector: Phase 2 semantic place search
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- trigram: Phase 2 "type a place name" fuzzy match

-- Phase 2 adds `pois.embedding vector(384)` plus an HNSW index. Deliberately
-- not created here: an unused column with no index is dead weight, and adding
-- a nullable column later is additive, not a rewrite. The extension is enabled
-- now so that migration needs no superuser.

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------
-- A trigger, not application code: it cannot drift out of sync the way an
-- ORM-maintained timestamp does when someone writes raw SQL.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- haversine_km — great-circle distance in kilometres
-- ---------------------------------------------------------------------------
-- IMMUTABLE + PARALLEL SAFE so it is legal in index expressions and generated
-- columns should we ever need it there. At 40-60 POIs per district this is
-- instant; PostGIS is deferred until Phase 3 needs real geodesics, and adding
-- a generated `geography` column then is additive.
CREATE OR REPLACE FUNCTION haversine_km(
    lat1 double precision, lon1 double precision,
    lat2 double precision, lon2 double precision
) RETURNS double precision
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$
    SELECT 6371.0088 * 2 * asin(sqrt(
        power(sin(radians(lat2 - lat1) / 2), 2)
      + cos(radians(lat1)) * cos(radians(lat2))
      * power(sin(radians(lon2 - lon1) / 2), 2)
    ));
$$;

-- ---------------------------------------------------------------------------
-- interest_tags — the single shared taxonomy (see note 2)
-- ---------------------------------------------------------------------------
CREATE TABLE interest_tags (
    id            smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug          text     NOT NULL UNIQUE,
    label         text     NOT NULL,
    kind          text     NOT NULL
        CHECK (kind IN ('interest', 'terrain', 'audience', 'season', 'product_category')),
    description   text,
    display_order smallint NOT NULL DEFAULT 100,
    is_active     boolean  NOT NULL DEFAULT true
);

COMMENT ON COLUMN interest_tags.kind IS
    'Partitions one vocabulary. Phase 1 uses ''interest''; Phase 5 marketplace adds ''product_category''.';

CREATE INDEX interest_tags_kind_idx ON interest_tags (kind) WHERE is_active;

-- ---------------------------------------------------------------------------
-- regions — state > district > taluk > locality (see note 3)
-- ---------------------------------------------------------------------------
CREATE TABLE regions (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent_id    bigint REFERENCES regions (id),
    kind         text   NOT NULL CHECK (kind IN ('state', 'district', 'taluk', 'locality')),
    name         text   NOT NULL,
    slug         text   NOT NULL UNIQUE,
    -- Materialised ancestry, always leading and trailing slash so that a
    -- prefix match cannot straddle a name boundary ('/x/ab/' vs '/x/abc/').
    path         text   NOT NULL UNIQUE CHECK (path LIKE '/%/'),
    centroid_lat double precision,
    centroid_lon double precision,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- text_pattern_ops is what makes `path LIKE '/karnataka/chikkamagaluru/%'`
-- an index scan under any collation.
CREATE INDEX regions_path_idx   ON regions (path text_pattern_ops);
CREATE INDEX regions_parent_idx ON regions (parent_id);

-- ---------------------------------------------------------------------------
-- pois — the supertype (see note 1)
-- ---------------------------------------------------------------------------
CREATE TABLE pois (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind      text NOT NULL CHECK (kind IN ('place', 'stay', 'activity')),
    name      text NOT NULL,
    slug      text NOT NULL UNIQUE,
    region_id bigint NOT NULL REFERENCES regions (id),

    lat double precision NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lon double precision NOT NULL CHECK (lon BETWEEN -180 AND 180),

    summary     text NOT NULL,   -- one line; this is what the LLM prompt carries
    description text,

    -- The engine budgets a day against this. Required for places/activities;
    -- meaningless for stays, hence nullable.
    typical_duration_minutes integer CHECK (typical_duration_minutes > 0),

    -- 1..5, deliberately comparable ACROSS kinds so one budget filter works on
    -- the whole candidate set. Absolute paise live alongside for display.
    cost_band      smallint CHECK (cost_band BETWEEN 1 AND 5),
    cost_min_paise bigint   CHECK (cost_min_paise >= 0),
    cost_max_paise bigint   CHECK (cost_max_paise >= 0),
    CONSTRAINT pois_cost_range_ordered CHECK (
        cost_min_paise IS NULL OR cost_max_paise IS NULL
        OR cost_max_paise >= cost_min_paise
    ),

    difficulty smallint CHECK (difficulty BETWEEN 1 AND 5),

    -- NULL means year-round. Otherwise the months (1-12) this is worth visiting;
    -- stage 1 filters on it so a monsoon-only waterfall isn't proposed in April.
    best_months smallint[],

    -- Most POIs must not appear twice in one itinerary. A few legitimately can
    -- (a town square you pass through, a base camp), so validation consults
    -- this flag rather than banning repeats outright.
    is_repeatable boolean NOT NULL DEFAULT false,

    media jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- Provenance. The Phase 1 seed set is hand-compiled, so unverified data
    -- must be VISIBLE rather than silently trusted: retrieval only ever selects
    -- status='published', and promotion is a deliberate act (`make publish`).
    source          text     NOT NULL,
    source_url      text,
    data_confidence smallint NOT NULL DEFAULT 3 CHECK (data_confidence BETWEEN 1 AND 5),
    verified_at     timestamptz,

    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published', 'archived')),

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER pois_set_updated_at BEFORE UPDATE ON pois
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- The stage 1 candidate query filters on exactly this pair first.
CREATE INDEX pois_status_kind_idx ON pois (status, kind);
CREATE INDEX pois_region_idx      ON pois (region_id);
-- Phase 2 "type a place name" fuzzy matching.
CREATE INDEX pois_name_trgm_idx   ON pois USING gin (name gin_trgm_ops);

-- --- kind-specific detail tables -------------------------------------------
-- 1:1 with pois, ON DELETE CASCADE. Splitting these out keeps the supertype
-- narrow and lets each kind carry genuinely different columns without a wide
-- table full of NULLs.

CREATE TABLE place_details (
    poi_id           uuid PRIMARY KEY REFERENCES pois (id) ON DELETE CASCADE,
    place_type       text NOT NULL
        CHECK (place_type IN ('viewpoint', 'temple', 'waterfall', 'fort', 'lake',
                              'trail', 'museum', 'garden', 'wildlife', 'town', 'other')),
    best_time_of_day text CHECK (best_time_of_day IN ('sunrise', 'morning', 'afternoon',
                                                      'sunset', 'evening', 'any')),
    opening_hours    jsonb,
    entry_fee_paise  bigint CHECK (entry_fee_paise >= 0),
    requires_permit  boolean NOT NULL DEFAULT false,
    notes            text
);

CREATE TABLE stay_details (
    poi_id              uuid PRIMARY KEY REFERENCES pois (id) ON DELETE CASCADE,
    stay_type           text NOT NULL
        CHECK (stay_type IN ('resort', 'homestay', 'hotel', 'campsite', 'guesthouse', 'other')),
    per_night_min_paise bigint CHECK (per_night_min_paise >= 0),
    per_night_max_paise bigint CHECK (per_night_max_paise >= 0),
    max_occupancy       smallint CHECK (max_occupancy > 0),
    meals_included      boolean NOT NULL DEFAULT false,
    amenities           jsonb   NOT NULL DEFAULT '[]'::jsonb,
    contact             jsonb   NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT stay_price_range_ordered CHECK (
        per_night_min_paise IS NULL OR per_night_max_paise IS NULL
        OR per_night_max_paise >= per_night_min_paise
    )
);

CREATE TABLE activity_details (
    poi_id             uuid PRIMARY KEY REFERENCES pois (id) ON DELETE CASCADE,
    activity_type      text NOT NULL
        CHECK (activity_type IN ('trek', 'rafting', 'safari', 'offroad', 'coffee_tour',
                                 'birding', 'cycling', 'kayaking', 'workshop', 'other')),
    physical_intensity smallint CHECK (physical_intensity BETWEEN 1 AND 5),
    min_age            smallint CHECK (min_age >= 0),
    requires_guide     boolean NOT NULL DEFAULT false,
    requires_booking   boolean NOT NULL DEFAULT false,
    operator_name      text,
    contact            jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- --- poi_tags ---------------------------------------------------------------
CREATE TABLE poi_tags (
    poi_id uuid     NOT NULL REFERENCES pois (id) ON DELETE CASCADE,
    tag_id smallint NOT NULL REFERENCES interest_tags (id),
    -- Tag membership is not boolean: Mullayanagiri and a flat garden walk are
    -- both 'trekking', but only one should lead a trekking itinerary. Stage 1
    -- ranks on this.
    weight smallint NOT NULL DEFAULT 50 CHECK (weight BETWEEN 1 AND 100),
    PRIMARY KEY (poi_id, tag_id)
);

-- Reverse direction: "every POI carrying these tags" is the stage 1 query.
CREATE INDEX poi_tags_tag_idx ON poi_tags (tag_id, weight DESC);

-- ---------------------------------------------------------------------------
-- guides
-- ---------------------------------------------------------------------------
CREATE TABLE guides (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,
    region_id       bigint NOT NULL REFERENCES regions (id),
    languages       text[] NOT NULL DEFAULT '{}',
    contact         jsonb  NOT NULL DEFAULT '{}'::jsonb,
    is_verified     boolean NOT NULL DEFAULT false,
    day_rate_paise  bigint CHECK (day_rate_paise >= 0),
    source          text NOT NULL,
    data_confidence smallint NOT NULL DEFAULT 3 CHECK (data_confidence BETWEEN 1 AND 5),
    status          text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published', 'archived')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER guides_set_updated_at BEFORE UPDATE ON guides
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX guides_region_idx ON guides (region_id) WHERE status = 'published';

-- Guides reuse the SAME interest_tags vocabulary (note 2).
CREATE TABLE guide_tags (
    guide_id uuid     NOT NULL REFERENCES guides (id) ON DELETE CASCADE,
    tag_id   smallint NOT NULL REFERENCES interest_tags (id),
    PRIMARY KEY (guide_id, tag_id)
);

-- "Guide details where available" — Phase 1 renders these read-only. Phase 4
-- booking adds tables BESIDE this one; it does not alter it.
CREATE TABLE poi_guides (
    poi_id   uuid NOT NULL REFERENCES pois (id) ON DELETE CASCADE,
    guide_id uuid NOT NULL REFERENCES guides (id) ON DELETE CASCADE,
    PRIMARY KEY (poi_id, guide_id)
);

-- ---------------------------------------------------------------------------
-- travel_estimates — the routing seam (see note 4)
-- ---------------------------------------------------------------------------
CREATE TABLE travel_estimates (
    from_poi_id      uuid NOT NULL REFERENCES pois (id) ON DELETE CASCADE,
    to_poi_id        uuid NOT NULL REFERENCES pois (id) ON DELETE CASCADE,
    -- 'static_haversine' (Phase 1) | 'maps_api' (Phase 3). In the PK so both
    -- coexist and the engine can prefer the better source per pair.
    source           text NOT NULL CHECK (source IN ('static_haversine', 'maps_api')),
    distance_km      numeric(7, 2) NOT NULL CHECK (distance_km >= 0),
    duration_minutes integer       NOT NULL CHECK (duration_minutes >= 0),
    computed_at      timestamptz   NOT NULL DEFAULT now(),
    PRIMARY KEY (from_poi_id, to_poi_id, source),
    CONSTRAINT travel_estimates_not_self CHECK (from_poi_id <> to_poi_id)
);

-- ---------------------------------------------------------------------------
-- users — exists now, empty in Phase 1 (see note 7)
-- ---------------------------------------------------------------------------
CREATE TABLE users (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email        text UNIQUE,
    display_name text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- trip_requests — what the user asked for, immutably
-- ---------------------------------------------------------------------------
CREATE TABLE trip_requests (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid REFERENCES users (id),  -- nullable: auth slots in later
    session_token text NOT NULL,              -- anonymous ownership in Phase 1

    mode text NOT NULL CHECK (mode IN ('interest', 'location', 'district')),

    -- Deliberately a denormalised array, not a join table: this is an immutable
    -- SNAPSHOT of the request. Retagging a POI later must not silently rewrite
    -- what someone asked for six months ago. The tradeoff (no FK) is accepted
    -- for that reason.
    tag_ids smallint[] NOT NULL DEFAULT '{}',

    region_id     bigint REFERENCES regions (id),  -- district/location modes
    anchor_poi_id uuid   REFERENCES pois (id),     -- location mode

    days        smallint NOT NULL CHECK (days BETWEEN 1 AND 14),
    party_size  smallint NOT NULL CHECK (party_size BETWEEN 1 AND 30),
    budget_band smallint NOT NULL CHECK (budget_band BETWEEN 1 AND 5),

    origin_label text NOT NULL,
    origin_lat   double precision NOT NULL CHECK (origin_lat BETWEEN -90 AND 90),
    origin_lon   double precision NOT NULL CHECK (origin_lon BETWEEN -180 AND 180),

    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX trip_requests_session_idx ON trip_requests (session_token, created_at DESC);

-- ---------------------------------------------------------------------------
-- itinerary_jobs — the async queue
-- ---------------------------------------------------------------------------
-- A Postgres table rather than Celery/Redis: job state is transactional with
-- the domain data, so a worker can never claim a job whose request rolled back,
-- and there is no second datastore to operate. Claimed with
-- FOR UPDATE SKIP LOCKED. Java analogue: Quartz's JDBC store, minus the
-- scheduler.
CREATE TABLE itinerary_jobs (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL REFERENCES trip_requests (id) ON DELETE CASCADE,

    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    -- Which pipeline stage is in flight. Purely for observability, but it turns
    -- "the plan is slow" into "stage 2 is slow" without a debugger.
    stage text CHECK (stage IN ('retrieval', 'compose', 'validate', 'route', 'assemble')),

    attempts     smallint NOT NULL DEFAULT 0,
    max_attempts smallint NOT NULL DEFAULT 3,

    locked_at timestamptz,
    locked_by text,
    run_after timestamptz NOT NULL DEFAULT now(),  -- retry backoff

    error_code   text,
    error_detail text,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER itinerary_jobs_set_updated_at BEFORE UPDATE ON itinerary_jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Partial index: the claim query only ever looks at queued rows, so the index
-- stays small no matter how much completed history accumulates.
CREATE INDEX itinerary_jobs_claim_idx ON itinerary_jobs (run_after)
    WHERE status = 'queued';
-- Reclaiming abandoned work (worker died mid-job) scans running rows by lock age.
CREATE INDEX itinerary_jobs_stale_idx ON itinerary_jobs (locked_at)
    WHERE status = 'running';
CREATE INDEX itinerary_jobs_request_idx ON itinerary_jobs (request_id);

-- ---------------------------------------------------------------------------
-- itineraries — the versioned output
-- ---------------------------------------------------------------------------
CREATE TABLE itineraries (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL REFERENCES trip_requests (id) ON DELETE CASCADE,

    version        smallint NOT NULL DEFAULT 1,
    -- Bumped by any breaking change to the payload shape. A stored payload can
    -- always be read back by the code that understands its schema_version.
    schema_version smallint NOT NULL,

    payload jsonb NOT NULL,

    -- 'llm' | 'deterministic' (see note 6)
    composer     text NOT NULL CHECK (composer IN ('llm', 'deterministic')),
    llm_provider text,
    llm_model    text,

    -- SHA-256 of the candidate ID set. Makes a run reproducible: same brief +
    -- same hash should yield a comparable itinerary.
    candidate_set_hash char(64),

    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (request_id, version)
);

CREATE INDEX itineraries_request_idx ON itineraries (request_id, version DESC);

-- --- itinerary_pois ---------------------------------------------------------
-- Not redundant with `payload`. This is the queryable audit trail proving every
-- rendered POI resolves to a real row (the "never invent places" guarantee,
-- checkable in SQL after the fact), and it powers "most-recommended places"
-- analytics without parsing jsonb.
CREATE TABLE itinerary_pois (
    itinerary_id uuid     NOT NULL REFERENCES itineraries (id) ON DELETE CASCADE,
    poi_id       uuid     NOT NULL REFERENCES pois (id),
    day_number   smallint NOT NULL CHECK (day_number >= 1),
    slot         smallint NOT NULL CHECK (slot >= 0),  -- 0 = the day's stay
    PRIMARY KEY (itinerary_id, day_number, slot)
);

CREATE INDEX itinerary_pois_poi_idx ON itinerary_pois (poi_id);
