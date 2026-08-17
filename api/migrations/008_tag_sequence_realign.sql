-- 008_tag_sequence_realign.sql — repair an exhausted smallint sequence.
--
-- THE BUG, because it is worth writing down.
--
-- `interest_tags.id` is a `smallint` (deliberately: `trip_requests.tag_ids` is a
-- `smallint[]` snapshot of what a user asked for, and a two-byte key is the right
-- size for a vocabulary of a few dozen tags). Its sequence therefore tops out at
-- 32767.
--
-- The seed loader upserts with `INSERT ... ON CONFLICT (slug) DO UPDATE`. Postgres
-- evaluates the column default — `nextval()` — BEFORE it detects the conflict, so
-- every re-seed of an existing tag consumes a sequence value and discards it. 24
-- tags per seed, and the integration tests re-seed the taxonomy for every test, so
-- a full test run burns hundreds. After enough runs the sequence hit its ceiling
-- and seeding failed with `SequenceGeneratorLimitExceededError` — on a table with
-- 30 rows in it.
--
-- Two changes, and both are needed:
--   1. this migration realigns the sequence to the actual maximum id
--   2. `store/seed.py` stops burning values: it looks the slug up and only inserts
--      when it is genuinely new
--
-- Without (2) this migration would just postpone the same failure.

SELECT setval(
    pg_get_serial_sequence('interest_tags', 'id'),
    COALESCE((SELECT max(id) FROM interest_tags), 0) + 1,
    false
);

COMMENT ON COLUMN interest_tags.id IS
    'smallint on purpose: trip_requests.tag_ids is a smallint[] snapshot. Sequence ceiling is 32767, so the seed loader must not consume a value per upsert — see migration 008.';
