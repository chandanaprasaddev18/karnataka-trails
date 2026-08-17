"""One-shot importer: a district/place spreadsheet -> seed YAML, with real coordinates.

Run with a path to an .xlsx shaped like
`~/Downloads/Karnataka_Popular_Districts_and_Places.xlsx`: a flat sheet of
district, place name and description.

WHY THIS EXISTS AS A COMMITTED TOOL RATHER THAN A THROWAWAY SCRIPT.
The seed files are the product's factual backbone, so how a row got there is part
of the row. This script is the answer to "where did these 25 places come from",
and the next district import will want it again.

WHAT THE SPREADSHEET DOES NOT CONTAIN, AND WHAT WE DO ABOUT IT.

* **Coordinates.** Its "Google Maps link" columns are search URLs
  (`/maps/search/?api=1&query=Mysore+Palace`) — a text query, not a position. A
  wrong coordinate silently corrupts every route, so we do not guess: each place
  is geocoded against OpenStreetMap's Nominatim (free, no key, the same data OSRM
  routes on), and a place that cannot be placed confidently is REPORTED AND
  SKIPPED rather than written with an approximate position.
* **Durations, costs, seasons, tags.** None are in the sheet. Durations and
  seasons are written from per-type defaults below, which is a judgement and is
  labelled as one: rows land as `status='draft'` with `confidence` set from how
  well the geocoder matched, and `make publish` still gates them.
* **Entry fees.** Left NULL. Money that changes and that we have not checked has
  no business being displayed.

USAGE
    python api/tools/import_directory.py <file.xlsx> [--out api/seeds] [--dry-run]

Nominatim's usage policy asks for at most one request a second and a real
User-Agent; both are honoured, and results are cached in
`<out>/.geocode_cache.json` so a re-run costs nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "karnataka-trails-seed/0.1 (trip planner seed importer; local dev)"
# Nominatim asks for <= 1 request per second. Slightly over, to be a good citizen.
REQUEST_INTERVAL_SECONDS = 1.2

# Karnataka's rough bounding box. A geocoder that returns Kerala for a Karnataka
# place name is a wrong answer, not a near miss: names repeat across state lines
# (there is a Kalhatti Falls in Tamil Nadu too), so a result outside this box is
# discarded rather than trusted.
KARNATAKA_BBOX = (11.5, 18.6, 74.0, 78.7)  # min_lat, max_lat, min_lon, max_lon

# Categories that are never the answer for a tourist place. This filter is not
# cosmetic: "Jog Falls" matched `highway` "Sirsi - Jog Falls Highway", a road 32 km
# from the waterfall, and scored well on name overlap while doing it.
REJECT_CATEGORIES = {"highway", "railway", "man_made", "barrier", "power"}
# A boundary is right for a district and wrong for a monument — EXCEPT that a
# national park IS a boundary in OSM, and rejecting those lost Bannerghatta. So
# boundaries are allowed only when they enclose something a visitor goes to.
PLACE_REJECT_CATEGORIES = REJECT_CATEGORIES | {"boundary"}
PLACE_ALLOWED_BOUNDARY_TYPES = {"national_park", "protected_area", "nature_reserve"}

# Per place-type defaults for the fields the spreadsheet does not carry. These are
# judgements, written down in one place so they can be reviewed as a set rather
# than argued about row by row.
#
#   duration  minutes a visitor typically spends
#   tags      interest slug -> weight (1-100)
#   months    when it is worth going; None means year round
#   type      place_details.place_type
TYPE_RULES: list[dict[str, Any]] = [
    {
        "match": r"\b(beach)\b",
        "type": "lake",  # closest existing place_type; coastline has none
        "duration": 120,
        "tags": {"nature": 85, "relaxation": 80, "photography": 70},
        "months": [10, 11, 12, 1, 2, 3],
    },
    {
        "match": r"\b(falls|waterfall)\b",
        "type": "waterfall",
        "duration": 90,
        "tags": {"nature": 90, "photography": 75},
        # Waterfalls run hardest just after the monsoon and dry out by March.
        "months": [8, 9, 10, 11, 12, 1],
    },
    {
        "match": r"\b(temple|shrine|math|monastery|church|basilica)\b",
        "type": "temple",
        "duration": 75,
        "tags": {"spiritual": 90, "heritage": 70},
        "months": None,
    },
    {
        "match": r"\b(palace|mahal|fort|stables|ruins|chariot)\b",
        "type": "fort",
        "duration": 120,
        "tags": {"heritage": 92, "photography": 70},
        "months": [10, 11, 12, 1, 2, 3],
    },
    {
        "match": r"\b(national park|wildlife|sanctuary|tiger reserve)\b",
        "type": "trail",
        "duration": 240,
        "tags": {"wildlife": 92, "nature": 85, "adventurous": 55},
        "months": [10, 11, 12, 1, 2, 3, 4],
    },
    {
        "match": r"\b(hill|peak|betta|giri|seat|view ?point)\b",
        "type": "viewpoint",
        "duration": 105,
        "tags": {"photography": 85, "nature": 80, "trekking": 60},
        "months": [10, 11, 12, 1, 2, 3],
    },
    {
        "match": r"\b(garden|gardens|park)\b",
        "type": "garden",
        "duration": 105,
        "tags": {"relaxation": 85, "nature": 70, "photography": 65},
        "months": None,
    },
    {
        "match": r"\b(caves?)\b",
        "type": "trail",
        "duration": 150,
        "tags": {"offbeat": 85, "adventurous": 70, "nature": 65},
        "months": [10, 11, 12, 1, 2, 3],
    },
    {
        "match": r"\b(zoo|zoological|camp|elephant)\b",
        "type": "museum",
        "duration": 180,
        "tags": {"wildlife": 80, "relaxation": 60},
        "months": None,
    },
]

FALLBACK_RULE: dict[str, Any] = {
    "type": "museum",
    "duration": 120,
    "tags": {"heritage": 60, "photography": 50},
    "months": None,
}


# ---------------------------------------------------------------------------
# spreadsheet
# ---------------------------------------------------------------------------


def read_sheets(path: Path) -> dict[str, list[list[str]]]:
    """Every sheet as rows of strings. No openpyxl: an xlsx is a zip of XML."""
    z = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        ss = ET.fromstring(z.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")) for si in ss]

    workbook = ET.fromstring(z.read("xl/workbook.xml"))
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}

    def text(cell: ET.Element) -> str:
        if cell.get("t") == "s":
            v = cell.find("m:v", NS)
            return shared[int(v.text)] if v is not None and v.text else ""
        if cell.get("t") == "inlineStr":
            return "".join(t.text or "" for t in cell.iter(f"{{{NS['m']}}}t"))
        v = cell.find("m:v", NS)
        return v.text or "" if v is not None else ""

    out: dict[str, list[list[str]]] = {}
    for sheet in workbook.find("m:sheets", NS) or []:
        part = (targets[sheet.get(rel_ns)] or "").lstrip("/")
        if not part.startswith("xl/"):
            part = f"xl/{part}"
        parsed = ET.fromstring(z.read(part))
        rows = [
            [text(c) for c in row.findall("m:c", NS)]
            for row in parsed.iter(f"{{{NS['m']}}}row")
        ]
        out[sheet.get("name") or part] = rows
    return out


def places_from(sheets: dict[str, list[list[str]]]) -> list[dict[str, str]]:
    """The flat place table, from whichever sheet carries one."""
    for name, rows in sheets.items():
        header_index = next(
            (i for i, r in enumerate(rows) if r and r[0].strip().lower().startswith("place id")),
            None,
        )
        if header_index is None:
            continue
        out = []
        for row in rows[header_index + 1 :]:
            if len(row) < 4 or not row[2].strip():
                continue
            out.append(
                {
                    "sheet": name,
                    "id": row[0].strip(),
                    "district": row[1].strip(),
                    "name": row[2].strip(),
                    "description": row[3].strip(),
                }
            )
        if out:
            return out
    raise SystemExit("no sheet with a 'Place ID' header was found")


def districts_from(sheets: dict[str, list[list[str]]]) -> list[dict[str, str]]:
    for rows in sheets.values():
        header_index = next(
            (
                i
                for i, r in enumerate(rows)
                if r and r[0].strip().lower().startswith("district id")
            ),
            None,
        )
        if header_index is None:
            continue
        out = []
        for row in rows[header_index + 1 :]:
            if len(row) < 3 or not row[1].strip():
                continue
            out.append({"name": row[1].strip(), "description": row[2].strip()})
        if out:
            return out
    return []


# ---------------------------------------------------------------------------
# geocoding
# ---------------------------------------------------------------------------


class Geocoder:
    """Nominatim, cached on disk, one request a second, honest about failure."""

    def __init__(self, cache_path: Path, aliases: dict[str, dict[str, Any]] | None = None) -> None:
        self.aliases = aliases or {}
        self.cache_path = cache_path
        self.cache: dict[str, Any] = (
            json.loads(cache_path.read_text()) if cache_path.exists() else {}
        )
        self._last_request = 0.0

    def _get(self, query: str) -> list[dict[str, Any]]:
        if query in self.cache:
            return self.cache[query]  # type: ignore[no-any-return]
        wait = REQUEST_INTERVAL_SECONDS - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        url = (
            f"{NOMINATIM}?"
            + urllib.parse.urlencode(
                {"q": query, "format": "jsonv2", "limit": "3", "addressdetails": "1"}
            )
        )
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                payload = json.loads(response.read())
        except Exception as exc:  # network, JSON, anything: a miss, not a crash
            print(f"    ! geocoder error for {query!r}: {exc}", file=sys.stderr)
            payload = []
        self._last_request = time.monotonic()
        self.cache[query] = payload
        self.cache_path.write_text(json.dumps(self.cache, indent=1))
        return payload  # type: ignore[no-any-return]

    def locate(
        self,
        name: str,
        district: str,
        *,
        allow_boundary: bool = False,
    ) -> dict[str, Any] | None:
        """Best plausible match for a place, or None.

        Several query forms are tried and the results unioned, because a single
        phrasing fails often: OSM knows "Lal Bagh", not "Lalbagh Botanical Garden
        Bengaluru". (The same lesson as the photo fetcher, which broke for exactly
        this reason.) Anything outside Karnataka is discarded.
        """
        bare = strip_parenthetical(name)
        district_core = strip_parenthetical(district)
        alias = self.aliases.get(name) or self.aliases.get(bare)
        queries = [
            f"{name}, {district_core}, Karnataka",
            f"{bare}, {district_core}, Karnataka",
            f"{bare}, Karnataka",
            bare,
        ]
        # A parenthetical is often the town — "Om Beach (Gokarna)" — which is a
        # better locality hint than the district.
        hint = parenthetical(name)
        if hint:
            queries.insert(0, f"{bare}, {hint}, Karnataka")
        # A reviewed alias wins outright: it exists because the plain forms were
        # wrong, not merely unlucky.
        if alias:
            queries.insert(0, str(alias["query"]))

        rejected = REJECT_CATEGORIES if allow_boundary else PLACE_REJECT_CATEGORIES

        seen: set[str] = set()
        for query in queries:
            if query in seen:
                continue
            seen.add(query)
            for hit in self._get(query):
                lat, lon = float(hit["lat"]), float(hit["lon"])
                if not in_karnataka(lat, lon):
                    continue
                if hit.get("category") in rejected and not (
                    hit.get("category") == "boundary"
                    and hit.get("type") in PLACE_ALLOWED_BOUNDARY_TYPES
                ):
                    continue
                # An alias records where it landed when it was reviewed. Drifting
                # off that is a signal the underlying map data moved, and it must
                # not pass silently.
                if alias and alias.get("expect"):
                    away = km_between(
                        (lat, lon), (float(alias["expect"][0]), float(alias["expect"][1]))
                    )
                    if away > float(alias.get("expect_within_km", 5)):
                        print(
                            f"    ! alias {name!r} resolved {away:.0f} km from its "
                            f"reviewed position — skipping, re-check the alias",
                            file=sys.stderr,
                        )
                        continue
                return {
                    "lat": round(lat, 5),
                    "lon": round(lon, 5),
                    "matched_query": query,
                    # A reviewed alias is somebody asserting that this map object is
                    # this place, which is exactly what the name check is a weak
                    # proxy for. So an alias hit is not penalised for a name
                    # mismatch: "ISKCON Temple Bangalore" resolves via "Hare Krishna
                    # Hill", which the source's own description names.
                    "via_alias": bool(alias) and query == str(alias.get("query")),
                    "osm_type": hit.get("osm_type"),
                    "osm_id": hit.get("osm_id"),
                    "display_name": hit.get("display_name"),
                    "category": hit.get("category"),
                    "type": hit.get("type"),
                    "name": hit.get("name") or "",
                    "address": hit.get("address") or {},
                }
        return None


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance. Duplicated from the app on purpose: a build tool that
    imports the application package would need its environment, and this is one
    formula."""
    from math import asin, cos, radians, sin, sqrt

    d_lat, d_lon = radians(b[0] - a[0]), radians(b[1] - a[1])
    h = sin(d_lat / 2) ** 2 + cos(radians(a[0])) * cos(radians(b[0])) * sin(d_lon / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(h))


def in_karnataka(lat: float, lon: float) -> bool:
    min_lat, max_lat, min_lon, max_lon = KARNATAKA_BBOX
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def parenthetical(text: str) -> str:
    match = re.search(r"\(([^)]+)\)", text)
    return match.group(1).strip() if match else ""


def strip_parenthetical(text: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", text).strip()


# ---------------------------------------------------------------------------
# YAML generation
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    normalised = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalised.lower()).strip("-")
    return re.sub(r"-+", "-", slug)


def rule_for(name: str, description: str) -> dict[str, Any]:
    """Which type defaults apply. The NAME decides, with the description as backup.

    Name first on purpose: "Dubare Elephant Camp" is a camp whatever its blurb
    says, and a description mentioning "temple" does not make a hill a temple.
    """
    for haystack in (name.lower(), description.lower()):
        for rule in TYPE_RULES:
            if re.search(rule["match"], haystack):
                return rule
    return FALLBACK_RULE


def confidence_for(place: dict[str, str], hit: dict[str, Any]) -> int:
    """How much to trust this row, 1 or 2. Never more, because nobody has checked it.

    2 = the OSM object's name lines up with the source's. Publishable (the gate's
        floor is 2) and flagged: `verified_at` stays NULL, which is what raises the
        itinerary's `unverified_data` warning.
    1 = it is inside Karnataka but the names do not line up. BELOW the publish
        floor, so it is written for a human to fix and cannot reach a traveller.

    The cap is the point. "Bengaluru Palace" matched the High Court and "Abbey
    Falls" matched something called "Kote Abbey falls" 9 km from the usual
    coordinate — a name that overlaps is not a position anyone has verified, and
    only a person looking at a map earns a 3.
    """
    if hit.get("via_alias"):
        return 2
    ours = strip_parenthetical(place["name"]).lower()
    theirs = (hit.get("name") or "").lower()
    if theirs and (theirs in ours or ours in theirs):
        return 2
    # Compare word sets: "Vijaya Vittala Temple" vs "Vittala Temple" should count.
    ours_words = {w for w in re.split(r"\W+", ours) if len(w) > 3}
    theirs_words = {w for w in re.split(r"\W+", theirs) if len(w) > 3}
    if ours_words and len(ours_words & theirs_words) >= 1:
        return 2
    return 1


def yaml_block(place: dict[str, str], hit: dict[str, Any], region_slug: str) -> str:
    rule = rule_for(place["name"], place["description"])
    slug = slugify(place["name"])
    confidence = confidence_for(place, hit)
    osm_url = (
        f"https://www.openstreetmap.org/{hit['osm_type']}/{hit['osm_id']}"
        if hit.get("osm_type") and hit.get("osm_id")
        else None
    )
    tags = ", ".join(f"{k}: {v}" for k, v in rule["tags"].items())
    months = rule["months"]

    lines = [
        f"- slug: {slug}",
        f"  name: {yaml_scalar(place['name'])}",
        f"  region: {region_slug}",
        f"  coords: [{hit['lat']}, {hit['lon']}]",
        f"  summary: {yaml_scalar(place['description'])}",
        f"  duration_minutes: {rule['duration']}",
        f"  tags: {{{tags}}}",
        "  source: karnataka_directory_xlsx+osm_nominatim",
    ]
    if osm_url:
        lines.append(f"  source_url: {osm_url}")
    lines.append(f"  confidence: {confidence}")
    if months:
        lines.append(f"  best_months: [{', '.join(str(m) for m in months)}]")
    lines += [
        "  place:",
        f"    type: {rule['type']}",
        "    # entry_fee_paise deliberately unset: not in the source, and money we",
        "    # have not checked must not be displayed.",
        f"    notes: {yaml_scalar('OSM match: ' + (hit.get('display_name') or '')[:160])}",
    ]
    return "\n".join(lines)


def yaml_scalar(text: str) -> str:
    """Quote a scalar safely. Long text becomes a folded block for readability."""
    clean = " ".join(text.split())
    if len(clean) > 88:
        wrapped = []
        line = "   "
        for word in clean.split():
            if len(line) + len(word) + 1 > 84:
                wrapped.append(line)
                line = "   "
            line += f" {word}"
        wrapped.append(line)
        return ">-\n" + "\n".join(f" {w}" for w in wrapped)
    if any(ch in clean for ch in ":#'\"{}[]&*!|>%@`") or clean != clean.strip():
        return json.dumps(clean, ensure_ascii=False)
    return clean


HEADER = """# Places to visit — {district}.
#
# ------------------------------------------------------------------------
# IMPORTED, NOT HAND-CURATED. NEEDS A FACT-CHECK PASS.
#
# Generated by `api/tools/import_directory.py` from
#   {source_file}
# with coordinates geocoded against OpenStreetMap (Nominatim). Rows load as
# status='draft' and are invisible to the engine until `make publish` promotes
# them; `verified_at` stays NULL after that, which is what makes the itinerary
# raise its `unverified_data` warning.
#
# WHAT CAME FROM THE SPREADSHEET: name, district, description.
# WHAT CAME FROM OPENSTREETMAP: coordinates, and the `notes` line naming the
#   object matched — check that line first, it is how you spot a wrong place.
# WHAT IS A JUDGEMENT MADE BY THE IMPORTER: duration_minutes, tags, best_months
#   and place type, all from per-type defaults in the importer. Review them.
# WHAT IS DELIBERATELY ABSENT: entry fees and cost bands. Money that changes and
#   that nobody has checked has no business being shown to a traveller.
#
# `confidence` here means how well the geocoder matched, never how true the
# description is: 3 = the OSM object's name lines up with ours, 2 = it is inside
# Karnataka but the names do not. Raise it only after checking the place yourself.
# ------------------------------------------------------------------------
"""


def load_aliases(path: Path) -> dict[str, dict[str, Any]]:
    """Parse the alias file.

    Hand-parsed rather than via PyYAML: this tool runs with the system Python so it
    can be used before `make install`, and the file's shape is three known keys.
    """
    if not path.exists():
        return {}
    aliases: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    key: str | None = None
    for raw in path.read_text().splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("- source_name:"):
            current = {"source_name": line.split(":", 1)[1].strip()}
            aliases[current["source_name"]] = current
            key = None
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith(("query:", "note:", "expect:", "expect_within_km:")):
            key, _, value = stripped.partition(":")
            value = value.strip()
            if key == "expect":
                current[key] = [float(x) for x in value.strip("[]").split(",")]
            elif key == "expect_within_km":
                current[key] = float(value)
            elif value in (">-", ">", "|"):
                current[key] = ""
            else:
                current[key] = value
        elif key in ("note", "query"):  # continuation of a folded block
            current[key] = f"{current.get(key, '')} {stripped}".strip()
    return aliases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("--out", type=Path, default=Path("api/seeds"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sheets = read_sheets(args.xlsx)
    places = places_from(sheets)
    districts = districts_from(sheets)
    print(f"read {len(places)} places across {len(districts) or '?'} districts")

    args.out.mkdir(parents=True, exist_ok=True)
    aliases = load_aliases(args.out / "geocode_aliases.yaml")
    print(f"loaded {len(aliases)} reviewed geocode alias(es)")
    geocoder = Geocoder(args.out / ".geocode_cache.json", aliases)

    # District centroids first: they anchor the region rows, and location mode's
    # "nearby" counts are measured from them.
    district_rows: list[dict[str, Any]] = []
    for district in districts:
        core = strip_parenthetical(district["name"])
        hit = geocoder.locate(core, core, allow_boundary=True)
        if hit is None:
            print(f"  SKIP district {district['name']}: could not geocode")
            continue
        district_rows.append(
            {
                "slug": slugify(core),
                "name": core,
                "centroid": [hit["lat"], hit["lon"]],
                "description": district["description"],
            }
        )
        print(f"  district {core:<22} {hit['lat']:>9},{hit['lon']:>9}  {hit['matched_query']}")

    by_district: dict[str, list[str]] = {}
    coords_by_district: dict[str, list[tuple[float, float]]] = {}
    skipped: list[str] = []
    for place in places:
        core = strip_parenthetical(place["district"])
        region_slug = slugify(core)
        hit = geocoder.locate(place["name"], place["district"])
        if hit is None:
            skipped.append(f"{place['name']} ({place['district']})")
            print(f"  SKIP {place['name']}: no plausible Karnataka match")
            continue
        confidence = confidence_for(place, hit)
        print(
            f"  {place['name'][:34]:<34} {hit['lat']:>9},{hit['lon']:>9} "
            f"c={confidence} {(hit.get('name') or '')[:28]}"
        )
        by_district.setdefault(region_slug, []).append(yaml_block(place, hit, region_slug))
        coords_by_district.setdefault(region_slug, []).append((hit["lat"], hit["lon"]))

    # A district centroid that disagrees with its own places is the near-miss this
    # importer is most likely to make: "Vijayanagara" resolves to a Mysuru
    # neighbourhood 200 km from Hampi, inside the state and past every other check.
    for row in district_rows:
        points = coords_by_district.get(row["slug"], [])
        if not points:
            continue
        median = (
            sorted(p[0] for p in points)[len(points) // 2],
            sorted(p[1] for p in points)[len(points) // 2],
        )
        away = km_between((row["centroid"][0], row["centroid"][1]), median)
        if away > 100:
            print(
                f"  ! {row['name']}: geocoded centroid is {away:.0f} km from the median "
                f"of its own places — using the places' median instead"
            )
            row["centroid"] = [round(median[0], 5), round(median[1], 5)]
            row["centroid_note"] = "median of this district's places (geocoded centroid disagreed)"

    print()
    for region_slug, blocks in by_district.items():
        name = next((d["name"] for d in district_rows if d["slug"] == region_slug), region_slug)
        body = HEADER.format(district=name, source_file=args.xlsx.name) + "\n" + "\n\n".join(blocks) + "\n"
        target = args.out / region_slug / "places.yaml"
        if args.dry_run:
            print(f"--- would write {target} ({len(blocks)} places)")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
            print(f"wrote {target} ({len(blocks)} places)")

    if district_rows and not args.dry_run:
        regions_file = args.out / ".imported_regions.yaml"
        regions_file.write_text(
            "# Region rows for the imported districts, to paste into regions.yaml.\n"
            "# Centroids are geocoded; review before merging.\n"
            + "".join(
                f"    - slug: {d['slug']}\n"
                f"      name: {d['name']}\n"
                f"      kind: district\n"
                f"      centroid: [{d['centroid'][0]}, {d['centroid'][1]}]"
                + (f"  # {d['centroid_note']}\n" if d.get("centroid_note") else "\n")
                for d in district_rows
            )
        )
        print(f"wrote {regions_file} (paste into regions.yaml after review)")

    if skipped:
        # Never silent: a place we could not place is a gap in the data, and the
        # person running this has to know which one.
        print(f"\n{len(skipped)} place(s) could NOT be geocoded and were skipped:")
        for name in skipped:
            print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
