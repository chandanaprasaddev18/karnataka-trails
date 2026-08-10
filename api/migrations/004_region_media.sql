-- 004_region_media.sql — hero imagery for regions.
--
-- WHY THIS EXISTS: `pois.media` already exists, but the home page needs a photo
-- per DISTRICT, and regions had nowhere to put one. Same jsonb array shape as
-- pois.media so one renderer handles both.
--
-- SHAPE (enforced in application code, not the database, because it is display
-- data rather than something we filter or join on):
--   [{
--     "url":          "https://upload.wikimedia.org/.../Foo.jpg",
--     "thumb_url":    "https://upload.wikimedia.org/.../800px-Foo.jpg",
--     "title":        "File:Foo.jpg",
--     "artist":       "Jane Doe",
--     "license":      "CC BY-SA 4.0",
--     "license_url":  "https://creativecommons.org/licenses/by-sa/4.0/",
--     "source_page":  "https://commons.wikimedia.org/wiki/File:Foo.jpg",
--     "width": 4000, "height": 3000
--   }]
--
-- `artist`, `license` and `source_page` are NOT optional in practice: these are
-- other people's photographs under Creative Commons terms, and attribution is a
-- licence condition, not a nicety. The renderer shows it; the fetcher refuses to
-- store an image whose licence it could not read.

ALTER TABLE regions ADD COLUMN media jsonb NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN regions.media IS
    'Hero imagery, same shape as pois.media. Every entry carries artist, license and source_page — attribution is a licence condition.';
