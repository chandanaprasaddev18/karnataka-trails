"""Invariants for imported seed data.

The Chikkamagaluru rows were hand-compiled: someone typed each coordinate and
could be asked why. The rows imported from the tourism spreadsheet were geocoded
by a script, and a geocoder fails in a specific way — it returns a confident,
well-formatted answer for the wrong object. Real examples caught during the import:

* "Jog Falls" matched a HIGHWAY named after it, 32 km from the waterfall
* "Bengaluru Palace" matched the High Court of Karnataka
* "ISKCON Temple" matched a BUS STOP of that name
* "Vijayanagara" matched a neighbourhood of Mysuru, 200 km from Hampi

Every one of those was inside Karnataka and looked plausible in a log line. These
tests are the standing version of the checks that caught them, so the next import
cannot quietly reintroduce one.
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from tripplan.config import get_settings
from tripplan.domain.models import GeoPoint
from tripplan.routing.base import haversine_km
from tripplan.store.seed import load_interest_tags, load_pois, load_regions, publish

# Karnataka's bounding box, matching the importer's. A place outside it is not a
# near miss: names repeat across state lines.
MIN_LAT, MAX_LAT, MIN_LON, MAX_LON = 11.5, 18.6, 74.0, 78.7

IMPORTED_SOURCE = "karnataka_directory_xlsx+osm_nominatim"


@pytest_asyncio.fixture
async def all_districts(db: asyncpg.Connection) -> asyncpg.Connection:
    """Every district that has a seed directory, loaded and published."""
    cfg = get_settings()
    await load_interest_tags(db, cfg.seeds_dir)
    await load_regions(db, cfg.seeds_dir)
    for path in sorted(cfg.seeds_dir.iterdir()):
        if path.is_dir() and (path / "places.yaml").exists():
            await load_pois(db, cfg.seeds_dir, path.name)
    await publish(db, min_confidence=2)
    return db


@pytest.mark.integration
async def test_every_published_place_is_inside_karnataka(
    all_districts: asyncpg.Connection,
) -> None:
    """The cheapest check that would have caught a cross-border mismatch."""
    strays = await all_districts.fetch(
        """
        SELECT slug, lat, lon FROM pois
        WHERE status = 'published'
          AND (lat NOT BETWEEN $1 AND $2 OR lon NOT BETWEEN $3 AND $4)
        """,
        MIN_LAT,
        MAX_LAT,
        MIN_LON,
        MAX_LON,
    )
    assert not strays, f"published outside Karnataka: {[r['slug'] for r in strays]}"


@pytest.mark.integration
async def test_imported_rows_cite_the_object_they_matched(
    all_districts: asyncpg.Connection,
) -> None:
    """A geocoded coordinate without a citation cannot be checked by anyone.

    `source_url` is the OSM object; the place's `notes` carries the display name
    that object had when it matched. Together they are how a reviewer confirms or
    refutes the position without re-running the importer.
    """
    rows = await all_districts.fetch(
        """
        SELECT p.slug, p.source_url, pd.notes
        FROM pois p LEFT JOIN place_details pd ON pd.poi_id = p.id
        WHERE p.source = $1
        """,
        IMPORTED_SOURCE,
    )
    assert rows, "expected imported rows in the seed set"
    for row in rows:
        assert row["source_url"], f"{row['slug']}: imported without an OSM citation"
        assert "openstreetmap.org" in str(row["source_url"])
        assert "OSM match:" in str(row["notes"] or ""), (
            f"{row['slug']}: no record of which map object this coordinate came from"
        )


@pytest.mark.integration
async def test_imported_rows_never_claim_to_be_verified(
    all_districts: asyncpg.Connection,
) -> None:
    """Nobody has checked these, and the data must say so.

    Confidence is capped at 2 by the importer and `verified_at` stays NULL, which is
    what makes an itinerary containing them raise `unverified_data`. A future import
    that awards itself a 3 would silence that warning.
    """
    rows = await all_districts.fetch(
        "SELECT slug, data_confidence, verified_at FROM pois WHERE source = $1",
        IMPORTED_SOURCE,
    )
    for row in rows:
        assert int(row["data_confidence"]) <= 2, (
            f"{row['slug']}: an imported row claims confidence "
            f"{row['data_confidence']} — only a human check earns 3 or more"
        )
        assert row["verified_at"] is None


@pytest.mark.integration
async def test_no_two_places_share_a_position(all_districts: asyncpg.Connection) -> None:
    """Two stops at one coordinate means one geocode answered for both.

    50 m, not zero: Hampi's monuments sit within 200 m of each other legitimately,
    so the threshold has to be tight enough to catch a duplicate and loose enough to
    leave a dense heritage site alone.
    """
    rows = await all_districts.fetch(
        "SELECT slug, name, lat, lon FROM pois WHERE status = 'published' AND kind = 'place'"
    )
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            metres = (
                haversine_km(
                    GeoPoint(lat=float(a["lat"]), lon=float(a["lon"])),
                    GeoPoint(lat=float(b["lat"]), lon=float(b["lon"])),
                )
                * 1000
            )
            assert metres > 50, (
                f"{a['name']} and {b['name']} are {metres:.0f} m apart — "
                "one geocode probably answered for both"
            )


@pytest.mark.integration
async def test_a_district_centroid_agrees_with_its_own_places(
    all_districts: asyncpg.Connection,
) -> None:
    """The check that caught Vijayanagara resolving to a Mysuru neighbourhood.

    A district centroid 200 km from every place we hold in that district is wrong,
    however plausible the geocoder found it. 150 km of slack because a large
    district's true centre can sit well away from its handful of famous sites.
    """
    districts = await all_districts.fetch(
        """
        SELECT d.slug, d.name, d.centroid_lat, d.centroid_lon,
               avg(p.lat) AS mean_lat, avg(p.lon) AS mean_lon, count(*) AS n
        FROM regions d
        JOIN regions r ON r.path LIKE d.path || '%'
        JOIN pois p ON p.region_id = r.id AND p.status = 'published'
        WHERE d.kind = 'district'
        GROUP BY d.slug, d.name, d.centroid_lat, d.centroid_lon
        """
    )
    assert districts, "expected districts with published places"
    for row in districts:
        away = haversine_km(
            GeoPoint(lat=float(row["centroid_lat"]), lon=float(row["centroid_lon"])),
            GeoPoint(lat=float(row["mean_lat"]), lon=float(row["mean_lon"])),
        )
        assert away < 150, (
            f"{row['name']}: centroid is {away:.0f} km from the mean of its "
            f"{row['n']} published places — one of the two is wrong"
        )


@pytest.mark.integration
async def test_every_published_place_can_be_planned(
    all_districts: asyncpg.Connection,
) -> None:
    """A place with no tags is invisible to interest mode, and a duration of NULL
    cannot be budgeted into a day. Both are silent — the place simply never
    appears — so they are asserted rather than left to be noticed."""
    rows = await all_districts.fetch(
        """
        SELECT p.slug, p.typical_duration_minutes,
               (SELECT count(*) FROM poi_tags t WHERE t.poi_id = p.id) AS tags
        FROM pois p
        WHERE p.status = 'published' AND p.kind IN ('place', 'activity')
        """
    )
    untagged = [r["slug"] for r in rows if int(r["tags"]) == 0]
    undurated = [r["slug"] for r in rows if r["typical_duration_minutes"] is None]
    assert not untagged, f"published but untagged, so unreachable by interest: {untagged}"
    assert not undurated, f"published with no duration, so unbudgetable: {undurated}"
