-- 003_trip_request_travel_month.sql — persist the month the user intends to travel.
--
-- WHY THIS EXISTS: the engine filters candidates on `pois.best_months`, so the
-- travel month materially changes the itinerary — in this district it decides
-- whether the treks are open at all. The API accepted a `travel_month` field
-- from the very first request, but there was nowhere to put it, so the worker
-- (which rebuilds the brief from `trip_requests`) fell back to the month the
-- request was CREATED.
--
-- The bug that exposed it: a November trekking request submitted in August came
-- back with "no places matched: trekking", because the worker planned for
-- August. Silently planning a different trip from the one asked for is the worst
-- class of bug this product can have, and it was invisible from the API contract.
--
-- Nullable with a COALESCE at read time rather than backfilled: for requests
-- made before this column existed, creation month IS the best available guess,
-- and inventing a value would misrepresent what those users actually asked for.

ALTER TABLE trip_requests ADD COLUMN travel_month smallint
    CHECK (travel_month BETWEEN 1 AND 12);

COMMENT ON COLUMN trip_requests.travel_month IS
    'Intended travel month (1-12). NULL for requests made before 003; readers fall back to created_at.';
