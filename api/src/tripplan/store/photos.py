"""Source real photographs from Wikimedia Commons.

Why Commons and not a stock-photo API: these are photographs of the *actual
places*, under Creative Commons licences, with a named author and a public source
page. A stock photo of "a misty hill" captioned Mullayanagiri is a small lie, and
this project has been careful not to tell those.

Two rules make it safe:

1. **A photo is only attached if the file's own TITLE names the place.** Commons
   search is fuzzy — querying "Kemmangundi" will happily return a nearby
   waterfall. Matching on the description or categories was tried and produced
   wrong attachments, because a description mentioning "Chikkamagaluru district"
   is not evidence that the photograph shows the place asked about. An absent
   photo costs us a nicer card; a wrong photo misinforms a traveller.

2. **Only places, never stays or activities.** A stay is a private property with
   no Commons photograph. An activity's name describes an action, not a location
   — "Birding morning at Bhadra" matched "Cormorant in the early morning sun" and
   put a Bangalore photo on a Bhadra activity. Both now fall back to district
   imagery in the UI.

3. **No licence, no photo.** Attribution is a condition of the licence, not a
   nicety, so a file whose author or licence cannot be read is skipped rather
   than stored with the fields left blank.

Nothing here runs at request time. `tripplan fetch-photos` is an offline step that
also DOWNLOADS each accepted image into the frontend's static directory, so no
page view ever touches Wikimedia. That matters for more than speed: hotlinking
their CDN returned 429 under even mild bursts, and leaning on a free service for
every page load is not something to ship.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from tripplan.db import DbConn
from tripplan.observability.logging import get_logger

log = get_logger(__name__)

COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Commons asks for a descriptive agent with contact details. Being a good citizen
# of a free service we are leaning on.
USER_AGENT = (
    "KarnatakaTripPlanner/0.1 (local development; "
    "https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia)"
)

# Licences we will actually display. Public-domain and CC variants are fine;
# anything else (or unreadable) is skipped rather than guessed at.
_ACCEPTABLE_LICENCE = re.compile(
    r"(^cc([\-\s]?by)?([\-\s]?sa)?([\-\s]?\d(\.\d)?)?$)|(^cc0)|(^public\s?domain)|(^pd)",
    re.IGNORECASE,
)

# Words that make a file a diagram, map, logo or portrait rather than a view of
# the place. Cheap and effective at removing the obvious wrong answers.
_REJECT_TITLE = re.compile(
    r"\b(map|locator|logo|coat of arms|flag|seal|signature|diagram|chart|graph|"
    r"portrait|stamp|coin|banknote|plaque|signboard|milestone|numeral|numerals|"
    r"glyph|script|alphabet|letter|letters|font|gazetteer|census|report|manuscript|"
    r"inscription text|title page|cover|"
    # Historical depictions: an empire's extent in 1485 is a map, and a title
    # carrying "c.1485" is a drawing or a plan rather than a photograph.
    r"empire|kingdom|dynasty|c\.\d{3,4})\b",
    re.IGNORECASE,
)

_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

# Commons category fragments that mean "this is not a photograph of the place".
# Checked against the file's own categories, which is the signal the title lacks.
_REJECT_CATEGORIES = re.compile(
    r"\b(maps?|cartograph|atlas|diagrams?|charts?|coats? of arms|flags?|logos?|"
    r"engravings?|lithographs?|drawings?|paintings?|illustrations?|"
    r"scanned images|old books|texts?|manuscripts?)\b",
    re.IGNORECASE,
)

# Place names that CONTRADICT this district. Karnataka shares waterfall and hill
# names with its neighbours, so a title can name our place and still be somewhere
# else entirely: "Kalhatty Falls ooty.jpg" is a Tamil Nadu waterfall that happens
# to share a name with the one near Kemmangundi. If a title mentions one of these
# and our own place does not, the candidate is rejected.
# Localities that belong to a district we now hold. A title naming one of these is
# not evidence the photo is somewhere else — it is evidence it is exactly where we
# think. Without this, "Panorama of Elephant Stables, Hampi.jpg" was rejected for
# being "in hampi, not here" while fetching for Vijayanagara district, which
# contains Hampi.
_DISTRICT_LOCALITIES: dict[str, set[str]] = {
    "vijayanagara": {"hampi", "hosapete", "hospet", "kamalapur", "anegundi"},
    "kodagu": {"coorg", "kodagu", "madikeri", "kushalnagar", "virajpet", "somwarpet"},
    "mysuru": {"mysore", "mysuru", "srirangapatna", "nanjangud"},
    "uttara-kannada": {
        "gokarna", "karwar", "murudeshwar", "murdeshwar", "sirsi", "honnavar",
        "dandeli", "yana", "kumta", "ankola",
    },
    "bengaluru-urban": {"bangalore", "bengaluru"},
    "chikkamagaluru": {"chikmagalur", "chikkamagaluru", "kadur", "tarikere", "sringeri"},
}


_CONTRADICTING_PLACES = {
    "ooty",
    "udhagamandalam",
    "kodaikanal",
    "munnar",
    "wayanad",
    "coorg",
    "kodagu",
    "madikeri",
    "mysore",
    "mysuru",
    "hampi",
    "gokarna",
    "goa",
    "kerala",
    "nilgiri",
    "nilgiris",
    "tamil",
    "andhra",
    "maharashtra",
    "shimla",
    "manali",
    "nepal",
    "himalaya",
    "sikkim",
    "darjeeling",
    "araku",
    "yercaud",
    "yelagiri",
}

# Tokens too generic to prove a match — "Falls" appears in dozens of unrelated
# files, so matching on it alone would attach the wrong waterfall.
_STOPWORDS = {
    "temple",
    "falls",
    "fall",
    "waterfall",
    "waterfalls",
    "peak",
    "hill",
    "hills",
    "lake",
    "fort",
    "park",
    "national",
    "wildlife",
    "sanctuary",
    "trek",
    "trail",
    "point",
    "view",
    "viewpoint",
    "walk",
    "ride",
    "safari",
    "museum",
    "the",
    "of",
    "and",
    "at",
    "in",
    "sri",
    "shri",
    "ghat",
    "river",
    "estate",
    "homestay",
    "resort",
    "lodge",
    "guesthouse",
    "camp",
    "campsite",
    "coffee",
    "plantation",
    "district",
    "karnataka",
    "india",
    # Time-of-day and action words. These caused a real false positive: "Birding
    # morning at Bhadra" matched "Cormorant in the early morning sun" on
    # "morning" and attached a Bangalore photo to a Bhadra activity.
    "morning",
    "evening",
    "afternoon",
    "sunrise",
    "sunset",
    "night",
    "early",
    "late",
    "day",
    "days",
    "birding",
    "climb",
    "descent",
    "cycling",
    "rafting",
    "boating",
    "kayaking",
    "tasting",
    "tour",
    "heritage",
    "backwater",
    "backwaters",
    "jeep",
    "night camp",
}


def _clean_url(value: str) -> str:
    """Drop Commons' analytics query string.

    The API appends `?utm_source=...&utm_campaign=imageinfo` to every URL. It is
    not part of the resource, it bloats stored payloads, and it makes the same
    image look like two different URLs to a cache.
    """
    return value.split("?", 1)[0]


class Photo(BaseModel):
    """One attributable image. Mirrors the jsonb shape documented in 004."""

    url: str
    thumb_url: str
    title: str
    artist: str
    license: str
    license_url: str | None = None
    source_page: str
    width: int | None = None
    height: int | None = None
    # Path the frontend serves the downloaded copy from, e.g. "/photos/foo.jpg".
    # Preferred over `url` at render time so no page view touches Wikimedia.
    local_path: str | None = None


@dataclass
class PhotoReport:
    matched: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)

    def render(self) -> str:
        lines = [
            f"attached photos to {len(self.matched)} record(s); "
            f"{len(self.unmatched)} left without one"
        ]
        if self.unmatched:
            lines.append(
                "  no confident match (left empty on purpose): " + ", ".join(sorted(self.unmatched))
            )
        if self.rejected:
            reasons: dict[str, list[str]] = {}
            for name, why in self.rejected.items():
                reasons.setdefault(why, []).append(name)
            for why, names in sorted(reasons.items()):
                lines.append(f"  rejected ({why}): {', '.join(sorted(names))}")
        lines.append(
            "  every stored photo carries artist + licence + source page; "
            "the UI renders them because attribution is a licence condition."
        )
        return "\n".join(lines)


def name_tokens(name: str) -> set[str]:
    """The distinctive words in a place name.

    "Annapoorneshwari Temple, Horanadu" -> {annapoorneshwari, horanadu}. Generic
    words are dropped so a match has to be on something that actually identifies
    the place.
    """
    # Three letters, not four. "Jog Falls" is one of the best-known places in the
    # state and was unmatchable: "jog" is three characters, "falls" is a stopword,
    # so the name had no distinctive token at all and fell back to its district.
    # Short noise words ("the", "and", "of") are already in _STOPWORDS.
    words = re.findall(r"[A-Za-z]{3,}", name.lower())
    return {w for w in words if w not in _STOPWORDS}


def _contradicts(title: str, own_text: str, district_slug: str = "") -> str | None:
    """Return the name of a place that puts this file somewhere else, if any.

    `district_slug` widens what counts as "here": Hampi is inside Vijayanagara
    district, so a file titled "…, Hampi.jpg" is confirming the location rather
    than contradicting it. Without that, adding a district silently rejected its
    own best photographs.
    """
    words: set[str] = set(re.findall(r"[a-z]+", title.lower()))
    mine: set[str] = set(re.findall(r"[a-z]+", own_text.lower()))
    mine |= _DISTRICT_LOCALITIES.get(district_slug, set())
    for clash in sorted(words & _CONTRADICTING_PLACES):
        if clash not in mine:
            return clash
    return None


def _identifies(candidate_text: str, tokens: set[str]) -> bool:
    """Does this file's own title name the place?

    Compared with spaces and punctuation stripped, because Commons is inconsistent
    about compound Kannada names: "Baba Budan Giri" and "Bababudangiri" are the
    same hill. Prefix matching on a 6-character stem then absorbs the remaining
    transliteration variance — Mullayanagiri / Mullayyanagiri / Mullaiyanagiri.
    """
    text = candidate_text.lower()
    squashed = re.sub(r"[^a-z]", "", text)
    for token in tokens:
        if token in text or token in squashed:
            return True
        stem = token[:6] if len(token) >= 7 else token
        if len(stem) >= 5 and (stem in text or stem in squashed):
            return True
    return False


def _licence_ok(license_name: str) -> bool:
    cleaned = license_name.strip().lower().replace("_", " ")
    return bool(_ACCEPTABLE_LICENCE.match(cleaned))


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


async def download(
    client: httpx.AsyncClient, photo: Photo, *, into: Path, public_prefix: str
) -> str | None:
    """Save the image locally and return its public path, or None on failure.

    Redistributing a copy is fine under CC BY-SA provided the author and licence
    travel with it, which they do — the renderer shows both on every image.

    The filename is content-addressed on the Commons file title, so re-running is
    idempotent and two POIs pointing at the same file share one copy on disk.
    """
    suffix = Path(photo.title).suffix.lower() or ".jpg"
    if suffix not in _EXTENSIONS:
        suffix = ".jpg"
    digest = hashlib.sha256(photo.title.encode("utf-8")).hexdigest()[:16]
    target = into / f"{digest}{suffix}"
    public_path = f"{public_prefix.rstrip('/')}/{target.name}"

    # File I/O goes through a thread: this is an async function, and a blocking
    # write would stall the event loop between downloads.
    if await asyncio.to_thread(_already_saved, target):
        return public_path

    try:
        response = await client.get(photo.thumb_url or photo.url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("photos.download_failed", title=photo.title, error=type(exc).__name__)
        return None

    await asyncio.to_thread(_save, target, response.content)
    return public_path


def _already_saved(target: Path) -> bool:
    return target.exists() and target.stat().st_size > 0


def _save(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def _rejected_by_category(page: dict[str, Any]) -> bool:
    """True when a file's own Commons categories mark it as not a photograph.

    Added after "Vijayanagara Empire c.1485.png" — a historical map — became a
    district's hero image. Its title contains none of the words the title filter
    looks for; its categories say "Old maps of India" immediately.
    """
    for category in page.get("categories") or []:
        title = str(category.get("title", ""))
        if _REJECT_CATEGORIES.search(title.removeprefix("Category:")):
            return True
    return False


class CommonsClient:
    """Thin read-only Commons client. Sequential and rate-limited by design."""

    def __init__(self, timeout: float = 30.0, pause_seconds: float = 0.4) -> None:
        self._timeout = timeout
        self._pause = pause_seconds

    async def search(self, client: httpx.AsyncClient, query: str, limit: int = 12) -> list[str]:
        response = await client.get(
            COMMONS_API,
            params={
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": query,
                "srnamespace": "6",  # File:
                "srlimit": str(limit),
            },
        )
        response.raise_for_status()
        data = response.json()
        return [r["title"] for r in data.get("query", {}).get("search", [])]

    async def image_info(
        self, client: httpx.AsyncClient, titles: list[str]
    ) -> dict[str, dict[str, Any]]:
        if not titles:
            return {}
        response = await client.get(
            COMMONS_API,
            params={
                "action": "query",
                "format": "json",
                "prop": "imageinfo|categories",
                "iiprop": "url|size|extmetadata",
                "iiurlwidth": "1200",
                # Categories are the reliable way to spot a map or a diagram: a
                # title filter cannot, because "Vijayanagara Empire c.1485.png" is
                # a historical MAP containing none of the words such a filter looks
                # for.
                #
                # `max`, not a number: cllimit is shared across ALL titles in the
                # request, so a small value silently returns categories for the
                # first few files and none for the rest. With 50 the map's own page
                # came back with an empty category list and sailed through.
                "cllimit": "max",
                "titles": "|".join(titles),
            },
        )
        response.raise_for_status()
        pages = response.json().get("query", {}).get("pages", {})
        return {p["title"]: p for p in pages.values() if "imageinfo" in p}

    async def best_photo(
        self,
        client: httpx.AsyncClient,
        name: str,
        extra_context: str = "",
        *,
        region_name: str = "",
        already_used: set[str] | None = None,
        want: int = 1,
        district_slug: str = "",
    ) -> tuple[list[Photo], str | None]:
        """Find up to `want` attributable photos that provably depict `name`.

        Returns ``(photos, rejection_reason)`` — the reason is for the report when
        nothing qualified, so a human can see WHY a place has no image.
        """
        tokens = name_tokens(name)
        if not tokens:
            # Names made entirely of generic words ("Z Point", "Coffee Museum")
            # cannot identify themselves, so require the locality instead. Without
            # this they are unmatchable by construction.
            tokens = name_tokens(region_name)
            if not tokens:
                return [], "name has no distinctive words"

        # Both query forms, unioned. The region-qualified query finds the right
        # file when a name is ambiguous, but it also returns plausible-looking
        # rubbish (district gazetteers) for names Commons indexes plainly. The
        # previous version only fell back to the bare name when the qualified
        # search returned NOTHING, so a page of irrelevant hits blocked the good
        # results entirely — that is why Baba Budangiri, which has dozens of
        # Commons photographs, ended up with none.
        queries = [f"{name} {extra_context}".strip(), name]
        titles: list[str] = []
        try:
            for query in queries:
                for title in await self.search(client, query):
                    if title not in titles:
                        titles.append(title)
                await asyncio.sleep(self._pause)
            info = await self.image_info(client, titles[:16])
        except httpx.HTTPError as exc:
            return [], f"commons unreachable ({type(exc).__name__})"

        found: list[Photo] = []
        reason = "no candidate identified the place"
        for title in titles:
            page = info.get(title)
            if page is None:
                continue
            if not title.lower().endswith(_EXTENSIONS):
                continue
            if _REJECT_TITLE.search(title):
                reason = "candidates were maps/diagrams"
                continue
            if _rejected_by_category(info.get(title, {})):
                reason = "candidates were maps/diagrams"
                continue
            if already_used is not None and title in already_used:
                # One photograph should not stand in for two different stops.
                continue
            clash = _contradicts(title, f"{name} {region_name}", district_slug)
            if clash is not None:
                reason = f"best candidate was in {clash}, not here"
                continue

            meta = page["imageinfo"][0]
            extra = meta.get("extmetadata", {})

            # RULE 1: the file's own TITLE must name the place. Deliberately
            # stricter than also searching the description or categories — a
            # description mentioning "Chikkamagaluru district" is not evidence
            # that the photograph shows the place we asked about, and allowing it
            # produced wrong attachments in practice.
            if not _identifies(title, tokens):
                continue

            # RULE 2: no readable licence, no photo.
            license_name = _strip_html(str(extra.get("LicenseShortName", {}).get("value", "")))
            artist = _strip_html(str(extra.get("Artist", {}).get("value", ""))) or "Unknown"
            if not license_name:
                reason = "licence could not be read"
                continue
            if not _licence_ok(license_name):
                reason = f"licence not displayable ({license_name})"
                continue

            found.append(
                Photo(
                    url=_clean_url(str(meta["url"])),
                    thumb_url=_clean_url(str(meta.get("thumburl") or meta["url"])),
                    title=title,
                    artist=artist,
                    license=license_name,
                    license_url=str(extra.get("LicenseUrl", {}).get("value", "")) or None,
                    source_page=str(meta.get("descriptionurl", "")),
                    width=meta.get("width"),
                    height=meta.get("height"),
                )
            )
            if len(found) >= want:
                break
        return found, (None if found else reason)


async def fetch_photos(
    conn: DbConn,
    *,
    district: str,
    photos_dir: Path,
    public_prefix: str,
    overwrite: bool = False,
    limit: int | None = None,
) -> PhotoReport:
    """Attach Commons photos to a district's regions and published POIs."""
    report = PhotoReport()
    commons = CommonsClient()
    # One file must not illustrate two different stops.
    used_titles: set[str] = set()

    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        # --- regions (district + taluks) ------------------------------------
        regions = await conn.fetch(
            """
            SELECT id, name, kind FROM regions
            WHERE (slug = $1 OR path LIKE (SELECT path FROM regions WHERE slug = $1) || '%')
              -- Localities too: Hampi is a locality holding every place in its
              -- district, and the itinerary uses a region's photo as the fallback
              -- for stops that have none of their own.
              AND kind IN ('district', 'taluk', 'locality')
              AND ($2 OR media = '[]'::jsonb)
            ORDER BY kind DESC, name
            """,
            district,
            overwrite,
        )
        # The district actually being fetched, for the search context and the
        # contradiction check. This was hardcoded to "Chikkamagaluru Karnataka",
        # which was harmless while there was one district and wrong the moment
        # there were six: every new district searched under the old one's name, and
        # a correct photo of Hampi was rejected for "being in Hampi, not here".
        district_name = str(
            await conn.fetchval("SELECT name FROM regions WHERE slug = $1", district) or district
        )
        for row in regions:
            photos, why = await commons.best_photo(
                client,
                str(row["name"]),
                f"{district_name} Karnataka landscape",
                region_name=f"{district_name} Karnataka",
                already_used=used_titles,
                want=2,
                district_slug=district,
            )
            label = f"{row['name']} ({row['kind']})"
            if not photos:
                report.unmatched.append(label)
                if why:
                    report.rejected[label] = why
                continue
            for photo in photos:
                photo.local_path = await download(
                    client, photo, into=photos_dir, public_prefix=public_prefix
                )
                used_titles.add(photo.title)
            await conn.execute(
                "UPDATE regions SET media = $2::jsonb WHERE id = $1",
                row["id"],
                [p.model_dump() for p in photos],
            )
            report.matched.append(label)

        # --- POIs -----------------------------------------------------------
        pois = await conn.fetch(
            """
            SELECT p.id, p.name, p.kind, r.name AS region_name
            FROM pois p
            JOIN regions r ON r.id = p.region_id
            WHERE p.status = 'published'
              AND r.path LIKE (SELECT path FROM regions WHERE slug = $1) || '%'
              AND ($2 OR p.media = '[]'::jsonb)
            ORDER BY p.kind, p.name
            """,
            district,
            overwrite,
        )
        if limit is not None:
            pois = pois[:limit]

        for row in pois:
            # Only PLACES get their own photograph.
            #
            # A stay is a private commercial property with no Commons photo, and a
            # generic hotel image is indistinguishable from a wrong one. An
            # activity's name describes an action rather than a location
            # ("Birding morning at Bhadra"), so any match is coincidental — that
            # is precisely how a Bangalore cormorant ended up on a Bhadra
            # activity. Both fall back to district imagery in the UI instead.
            if row["kind"] in ("stay", "activity"):
                report.unmatched.append(str(row["name"]))
                report.rejected[str(row["name"])] = (
                    f"{row['kind']}s do not get their own photo — name describes a "
                    "property or an action, not a place"
                )
                continue

            photos, why = await commons.best_photo(
                client,
                str(row["name"]),
                f"{row['region_name']} Karnataka",
                region_name=f"{row['region_name']} {district_name} Karnataka",
                district_slug=district,
                already_used=used_titles,
                want=3,
            )
            if not photos:
                report.unmatched.append(str(row["name"]))
                if why:
                    report.rejected[str(row["name"])] = why
                continue
            for photo in photos:
                photo.local_path = await download(
                    client, photo, into=photos_dir, public_prefix=public_prefix
                )
                used_titles.add(photo.title)
            await conn.execute(
                "UPDATE pois SET media = $2::jsonb WHERE id = $1",
                row["id"],
                [p.model_dump() for p in photos],
            )
            report.matched.append(str(row["name"]))

        # A taluk with no photograph of its own borrows one from a published place
        # inside it. Honest by construction — the photo IS in that taluk — and the
        # UI labels a borrowed image as showing the surrounding area.
        borrowed = await conn.fetch(
            """
            UPDATE regions r
            SET media = sub.media
            FROM (
                SELECT rr.id AS region_id, p.media
                FROM regions rr
                JOIN pois p ON p.region_id = rr.id
                WHERE rr.kind = 'taluk'
                  AND rr.media = '[]'::jsonb
                  AND p.status = 'published'
                  AND p.kind = 'place'
                  AND p.media <> '[]'::jsonb
                ORDER BY rr.id, p.data_confidence DESC
            ) AS sub
            WHERE r.id = sub.region_id AND r.media = '[]'::jsonb
            RETURNING r.name
            """
        )
        for row in borrowed:
            report.matched.append(f"{row['name']} (taluk, borrowed from a place inside it)")
            report.unmatched = [u for u in report.unmatched if not u.startswith(str(row["name"]))]

    log.info(
        "photos.fetched",
        matched=len(report.matched),
        unmatched=len(report.unmatched),
        district=district,
        saved_to=str(photos_dir),
    )
    return report
