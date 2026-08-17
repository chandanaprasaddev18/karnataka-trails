-- 007_marketplace.sql — Phase 5: hyperlocal products, tagged by district and interest.
--
-- The Phase 5 requirement was a marketplace for hyperlocal products "tagged by
-- district and interest", reusing both existing taxonomies. That is what this
-- does: `region_id` for the district, `interest_tags` for the category, and
-- `interest_tags.kind = 'product_category'` rows added by SEED rather than by
-- schema — which is the payoff for partitioning that table by `kind` in
-- migration 001 instead of building three vocabularies.
--
-- THE HARD PART IS NOT THE SCHEMA, IT IS THE DATA.
--
-- A marketplace needs sellers, and we have none. Inventing "Malnad Coffee Co,
-- +91 98xxx" would be the worst thing this codebase could do: a traveller could
-- try to pay a business that does not exist, and a real trader's number could be
-- guessed into a seed file by accident. So `vendors` and `products` follow exactly
-- the rule `guides` already follows — `source='placeholder'` rows are refused by
-- `publish` and can never reach a traveller.
--
-- That would leave the marketplace empty, so the useful half is separated out:
--
--   region_specialities — WHAT a district is known for, with no seller attached.
--
-- "Chikkamagaluru grows arabica; Mudigere makes jaggery" is a checkable fact about
-- a place, not a commercial offer. It needs no vendor, it is what a traveller
-- actually wants on an itinerary ("pick this up while you are there"), and it is
-- the honest thing we can ship today. When a real vendor consents to being listed,
-- their products appear beside the speciality they belong to and nothing else
-- changes.
--
-- No prices on specialities, deliberately. A price without a seller is a guess.

-- --- vendors ----------------------------------------------------------------

CREATE TABLE vendors (
    id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE,
    name text NOT NULL,

    region_id bigint REFERENCES regions (id),
    -- Where you actually find them. A market stall has no street address, so this
    -- is free text rather than a structured field we would half-populate.
    address   text,
    contact   jsonb NOT NULL DEFAULT '{}'::jsonb,

    -- Same provenance triple as `pois` and `guides`, so one `publish` rule covers
    -- everything a traveller can be shown.
    source          text NOT NULL,
    source_url      text,
    data_confidence smallint NOT NULL DEFAULT 3 CHECK (data_confidence BETWEEN 1 AND 5),
    verified_at     timestamptz,
    status          text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published', 'retired')),

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX vendors_region_idx ON vendors (region_id) WHERE status = 'published';

CREATE TRIGGER vendors_touch
    BEFORE UPDATE ON vendors FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- --- products ---------------------------------------------------------------

CREATE TABLE products (
    id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug      text NOT NULL UNIQUE,
    vendor_id uuid NOT NULL REFERENCES vendors (id) ON DELETE CASCADE,

    name    text NOT NULL,
    summary text,
    -- Integer paise, like every other price in this schema. Nullable because
    -- "market rate" is a real answer for produce.
    price_paise bigint CHECK (price_paise IS NULL OR price_paise >= 0),
    unit        text,

    -- Denormalised from the vendor so a product can be tied to where it is MADE
    -- when that differs from where it is sold.
    region_id bigint REFERENCES regions (id),
    media     jsonb NOT NULL DEFAULT '[]'::jsonb,

    source          text NOT NULL,
    source_url      text,
    data_confidence smallint NOT NULL DEFAULT 3 CHECK (data_confidence BETWEEN 1 AND 5),
    verified_at     timestamptz,
    status          text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'published', 'retired')),

    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX products_region_idx ON products (region_id) WHERE status = 'published';
CREATE INDEX products_vendor_idx ON products (vendor_id);

CREATE TRIGGER products_touch
    BEFORE UPDATE ON products FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- --- tagging, reusing interest_tags -----------------------------------------

CREATE TABLE vendor_tags (
    vendor_id uuid     NOT NULL REFERENCES vendors (id) ON DELETE CASCADE,
    tag_id    smallint NOT NULL REFERENCES interest_tags (id),
    weight    smallint NOT NULL DEFAULT 50 CHECK (weight BETWEEN 1 AND 100),
    PRIMARY KEY (vendor_id, tag_id)
);

CREATE TABLE product_tags (
    product_id uuid     NOT NULL REFERENCES products (id) ON DELETE CASCADE,
    tag_id     smallint NOT NULL REFERENCES interest_tags (id),
    weight     smallint NOT NULL DEFAULT 50 CHECK (weight BETWEEN 1 AND 100),
    PRIMARY KEY (product_id, tag_id)
);

CREATE INDEX product_tags_tag_idx ON product_tags (tag_id);

-- --- what a place is known for ----------------------------------------------
-- The seller-free core of the marketplace, and the only part with data today.

CREATE TABLE region_specialities (
    region_id bigint   NOT NULL REFERENCES regions (id) ON DELETE CASCADE,
    tag_id    smallint NOT NULL REFERENCES interest_tags (id),

    -- One line a traveller can act on: what it is, when it is good, what to look
    -- for. Not marketing copy — if we cannot say something specific, the row
    -- should not exist.
    note text NOT NULL,
    -- Months when buying it makes sense (harvest, pressing season). NULL = any time.
    best_months smallint[] CHECK (best_months IS NULL OR array_length(best_months, 1) > 0),

    source          text NOT NULL,
    source_url      text,
    data_confidence smallint NOT NULL DEFAULT 3 CHECK (data_confidence BETWEEN 1 AND 5),

    display_order smallint NOT NULL DEFAULT 100,
    created_at    timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (region_id, tag_id)
);

CREATE INDEX region_specialities_tag_idx ON region_specialities (tag_id);

COMMENT ON TABLE region_specialities IS
    'What a district or taluk is known for producing, keyed to a product_category tag. No seller and no price attached — a price without a seller is a guess.';
COMMENT ON TABLE vendors IS
    'Sellers. Follows the same provenance/publish rule as pois and guides: a source=placeholder row is refused by publish and can never reach a traveller.';
