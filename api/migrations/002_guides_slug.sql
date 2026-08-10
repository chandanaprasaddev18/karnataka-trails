-- 002_guides_slug.sql — give guides the natural key their seed file already has.
--
-- WHY THIS EXISTS: 001 created `guides` with only a surrogate uuid primary key.
-- The seed loader needs a natural key to upsert on, or a re-run inserts a
-- duplicate row every time (an `ON CONFLICT (id)` on a generated key never
-- fires). Every other seeded table already upserts on `slug`; guides should
-- not be the exception.
--
-- Two real guides can legitimately share a name, so name+region is NOT a safe
-- key — the slug is authored deliberately in guides.yaml and is the right one.
--
-- Backfill note: this runs before any guide rows exist in any environment, so
-- the NOT NULL is added directly rather than in the usual
-- add-nullable / backfill / set-not-null dance.

ALTER TABLE guides ADD COLUMN slug text;

-- Defensive: if a future environment somehow has rows, give them a slug derived
-- from the id so the NOT NULL below cannot fail.
UPDATE guides SET slug = 'guide-' || left(id::text, 8) WHERE slug IS NULL;

ALTER TABLE guides ALTER COLUMN slug SET NOT NULL;
ALTER TABLE guides ADD CONSTRAINT guides_slug_key UNIQUE (slug);
