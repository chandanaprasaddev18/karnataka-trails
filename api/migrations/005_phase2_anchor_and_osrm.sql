-- 005_phase2_anchor_and_osrm.sql — Phase 2 planning modes, and a real routing source.
--
-- TWO INDEPENDENT CHANGES, both additive, both needed by the same release.
--
-- 1. LOCATION MODE NEEDS AN ANCHOR POINT AND A RADIUS.
--
-- `trip_requests.anchor_poi_id` already exists, and it is not enough. "Plan
-- around here" has two legitimate anchors: a POI we publish (Mullayanagiri), and
-- a locality that is not a POI at all (the town of Mudigere). Forcing the second
-- case through a POI foreign key would mean inventing POI rows for towns, which
-- is exactly the kind of fiction this schema exists to prevent.
--
-- So the anchor is stored as what the engine actually consumes — a labelled
-- point — plus the radius the user chose. `anchor_poi_id` stays and is populated
-- when the anchor IS a POI, so the audit trail keeps the foreign key where one
-- honestly exists.
--
-- Nullable, because interest and district modes have no anchor. A CHECK enforces
-- the pairing instead: an anchor is a lat AND a lon AND a label, or nothing.
--
-- 2. OSRM IS A DISTINCT ROUTING SOURCE FROM "SOME MAPS API".
--
-- The original CHECK allowed 'static_haversine' and 'maps_api'. Recording real
-- OSRM road distances as the generic 'maps_api' would throw away the one thing
-- provenance is for: knowing WHICH provider said 249 km. A future Google or
-- Mapbox provider is a different source with different licensing and different
-- accuracy, and `source` is in the primary key precisely so they can coexist and
-- be compared. So OSRM gets its own value.
--
-- Widening a CHECK is safe: every existing row still satisfies it.

-- --- 1. location-mode anchor -------------------------------------------------

ALTER TABLE trip_requests
    ADD COLUMN anchor_label text,
    ADD COLUMN anchor_lat   double precision CHECK (anchor_lat BETWEEN -90 AND 90),
    ADD COLUMN anchor_lon   double precision CHECK (anchor_lon BETWEEN -180 AND 180),
    -- Radius in km. Bounded: under 5 km nothing is reachable in this district,
    -- and over 200 km "around here" has stopped meaning anything.
    ADD COLUMN radius_km    smallint CHECK (radius_km BETWEEN 5 AND 200);

ALTER TABLE trip_requests
    ADD CONSTRAINT trip_requests_anchor_complete CHECK (
        (anchor_label IS NULL AND anchor_lat IS NULL AND anchor_lon IS NULL)
        OR (anchor_label IS NOT NULL AND anchor_lat IS NOT NULL AND anchor_lon IS NOT NULL)
    );

COMMENT ON COLUMN trip_requests.anchor_label IS
    'Location mode: the anchor as the user named it. Free text because an anchor may be a locality that is not a POI.';
COMMENT ON COLUMN trip_requests.radius_km IS
    'Location mode: how far from the anchor the user is willing to travel.';

-- --- 2. OSRM as its own routing source --------------------------------------

ALTER TABLE travel_estimates
    DROP CONSTRAINT travel_estimates_source_check;

ALTER TABLE travel_estimates
    ADD CONSTRAINT travel_estimates_source_check
    CHECK (source IN ('static_haversine', 'osrm', 'maps_api'));

COMMENT ON COLUMN travel_estimates.source IS
    'Who measured this leg: static_haversine (placeholder), osrm (real road network), maps_api (reserved for a commercial provider). In the PK so they coexist and stay comparable.';
