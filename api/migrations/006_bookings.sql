-- 006_bookings.sql — Phase 4: booking requests for stays, guides and activities.
--
-- WHAT THIS CAN AND CANNOT DO, AND WHY THE SCHEMA LOOKS LIKE THIS.
--
-- We hold no verified contact for any stay (`stays.yaml` leaves `contact` empty
-- on purpose — a wrong phone number misdirects a real traveller) and every seeded
-- guide is an explicit placeholder. There is no partner API, no inventory feed and
-- no payment provider. So this app CANNOT confirm a booking, and it must never
-- render a status that implies otherwise.
--
-- What it can honestly do is record the request: "these dates, this party, this
-- stay". That is a real artefact — it is what a traveller wants to keep, what a
-- future partner integration fulfils, and what "My bookings" lists. The status
-- vocabulary is therefore built around who is waiting on whom:
--
--   requested   the traveller has recorded it; we have not sent it anywhere,
--               because we have nobody verified to send it to
--   sent        handed to the property/guide through a real channel (no code path
--               creates this yet; it exists so adding one is not a migration)
--   confirmed   the property agreed. ONLY a partner integration may write this.
--   declined    the property said no
--   withdrawn   the traveller changed their mind
--
-- `requested` is the only status this release can produce. A CHECK cannot enforce
-- "the app must not lie", but naming the states precisely means the lie would have
-- to be written deliberately rather than fallen into.
--
-- OWNERSHIP is by `session_token`, matching `trip_requests`. Phase 4 in the plan
-- said "booking" and the plan also said auth slots in later; a booking keyed to a
-- browser session is the honest version of that — it works today and `user_id`
-- backfills from `trip_requests` when accounts arrive.
--
-- THE CONTACT SNAPSHOT is copied in, not joined. What the traveller was shown at
-- the time is part of the record: if a property's number changes next month, the
-- request must still say what we told them, or the audit trail is worthless.

CREATE TABLE bookings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Ownership. Nullable user_id for the same reason trip_requests has one.
    session_token text NOT NULL,
    user_id       uuid REFERENCES users (id),

    -- What this is a request for. A booking always points at a POI or a guide,
    -- never at free text — the same rule that stops an itinerary inventing places.
    kind     text NOT NULL CHECK (kind IN ('stay', 'guide', 'activity')),
    poi_id   uuid REFERENCES pois (id),
    guide_id uuid REFERENCES guides (id),

    -- Where it came from, so "my bookings" can show the trip it belongs to.
    -- Nullable: a booking made from a place page has no itinerary.
    itinerary_id uuid REFERENCES itineraries (id) ON DELETE SET NULL,
    day_number   smallint CHECK (day_number BETWEEN 1 AND 14),

    status text NOT NULL DEFAULT 'requested'
        CHECK (status IN ('requested', 'sent', 'confirmed', 'declined', 'withdrawn')),

    -- The ask.
    party_size smallint NOT NULL CHECK (party_size BETWEEN 1 AND 30),
    check_in   date,
    check_out  date,
    note       text,

    -- What we showed the traveller at the time: name, locality, price band, and
    -- any contact we had. Frozen deliberately (see header).
    target_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- Set when a real channel was used. NULL is the honest default and the UI
    -- reads it: no channel means "we could not send this for you".
    sent_via text,
    sent_at  timestamptz,

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),

    -- Exactly one target, matching `kind`. A stay booking pointing at a guide is
    -- a bug that would otherwise surface as a confusing empty card.
    CONSTRAINT bookings_target_matches_kind CHECK (
        (kind IN ('stay', 'activity') AND poi_id IS NOT NULL AND guide_id IS NULL)
        OR (kind = 'guide' AND guide_id IS NOT NULL AND poi_id IS NULL)
    ),
    -- A stay you leave before you arrive is not a stay.
    CONSTRAINT bookings_dates_ordered CHECK (
        check_in IS NULL OR check_out IS NULL OR check_out > check_in
    ),
    -- `sent` and anything after it requires a channel. This is the one place the
    -- database can hold the line on the honesty rule above.
    CONSTRAINT bookings_sent_needs_channel CHECK (
        status IN ('requested', 'withdrawn') OR sent_via IS NOT NULL
    )
);

-- The two queries this table serves: "my bookings, newest first" and "everything
-- for this trip".
CREATE INDEX bookings_session_idx ON bookings (session_token, created_at DESC);
CREATE INDEX bookings_itinerary_idx ON bookings (itinerary_id) WHERE itinerary_id IS NOT NULL;

-- One open request per traveller per target per trip. Without this, a double-click
-- on "request this stay" produces two identical rows and the list looks broken.
-- Partial, so a withdrawn request does not block asking again.
CREATE UNIQUE INDEX bookings_one_open_per_target ON bookings (
    session_token,
    kind,
    COALESCE(poi_id, guide_id),
    COALESCE(itinerary_id, '00000000-0000-0000-0000-000000000000'::uuid)
) WHERE status NOT IN ('withdrawn', 'declined');

CREATE TRIGGER bookings_touch
    BEFORE UPDATE ON bookings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE bookings IS
    'Booking REQUESTS. This app cannot confirm a booking: no verified contacts, no partner API, no payments. Only a partner integration may write status=confirmed.';
COMMENT ON COLUMN bookings.target_snapshot IS
    'What the traveller was shown when they asked. Copied, not joined — the record must not silently change when the underlying row does.';
COMMENT ON COLUMN bookings.sent_via IS
    'The real channel a request was handed to, or NULL. NULL means we recorded the request but could not deliver it, and the UI says so.';
