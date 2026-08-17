"""The marketplace — Phase 5.

Two halves, and the split is the point:

* **Specialities** — what a place is known for producing. A fact about a district,
  keyed to a `product_category` tag, with no seller and no price. This is the half
  that has data today, and it is genuinely useful: it answers "what should I bring
  back from Mudigere" while standing in Mudigere.

* **Vendors and products** — who sells it. This half is empty on purpose. Every
  seeded vendor is a placeholder that `publish` refuses, because a vendor is a
  commercial party a traveller might try to pay, and inventing one could send money
  or a phone call to a business that does not exist. The code path is complete and
  tested, so a real vendor appears the moment one consents to being listed.

Both are tagged by district (`region_id`) and by interest (`interest_tags`), which
is what Phase 5 asked for, reusing the taxonomy from migration 001 unchanged.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from tripplan.db import DbConn
from tripplan.observability.logging import get_logger

log = get_logger(__name__)


class ProductOut(BaseModel):
    """A product from a published vendor. None exist yet — see the module docstring."""

    slug: str
    name: str
    summary: str | None = None
    price_note: str | None = None
    unit: str | None = None
    vendor_name: str
    vendor_slug: str
    # Empty until a real vendor consents. The UI must not render an empty block as
    # if it were a contact card.
    vendor_contact: dict[str, Any] = Field(default_factory=dict)
    region_name: str | None = None
    categories: list[str] = Field(default_factory=list)


class Speciality(BaseModel):
    """What a region is known for. The seller-free half of the marketplace."""

    region_slug: str
    region_name: str
    region_kind: str
    category_slug: str
    category_label: str
    note: str
    best_months: list[int] = Field(default_factory=list)
    source: str
    data_confidence: int
    # Published products in this category from this region. Empty today, and the
    # count is rendered so "we list no sellers" is visible rather than implied.
    products: list[ProductOut] = Field(default_factory=list)


async def specialities(
    conn: DbConn,
    *,
    district_slug: str | None = None,
    region_slugs: list[str] | None = None,
) -> list[Speciality]:
    """Specialities, optionally scoped to a district's whole catchment or to regions.

    `district_slug` uses the materialised `regions.path`, so asking for
    Chikkamagaluru returns its taluks' specialities too — the same ancestry trick
    the planning modes use. `region_slugs` is the narrow form, used by an itinerary
    that only passes through three taluks.
    """
    rows = await conn.fetch(
        """
        SELECT r.slug AS region_slug, r.name AS region_name, r.kind AS region_kind,
               it.slug AS category_slug, it.label AS category_label,
               rs.note, rs.best_months, rs.source, rs.data_confidence, rs.display_order
        FROM region_specialities rs
        JOIN regions r ON r.id = rs.region_id
        JOIN interest_tags it ON it.id = rs.tag_id
        WHERE ($1::text IS NULL OR r.path LIKE (
                   SELECT path FROM regions WHERE slug = $1
               ) || '%')
          AND ($2::text[] IS NULL OR r.slug = ANY($2::text[]))
        ORDER BY rs.display_order, it.display_order, r.name
        """,
        district_slug,
        region_slugs,
    )

    out: list[Speciality] = []
    for row in rows:
        products = await products_for(
            conn, category_slug=str(row["category_slug"]), region_slug=str(row["region_slug"])
        )
        out.append(
            Speciality(
                region_slug=str(row["region_slug"]),
                region_name=str(row["region_name"]),
                region_kind=str(row["region_kind"]),
                category_slug=str(row["category_slug"]),
                category_label=str(row["category_label"]),
                note=str(row["note"]),
                best_months=list(row["best_months"] or []),
                source=str(row["source"]),
                data_confidence=int(row["data_confidence"]),
                products=products,
            )
        )
    return out


async def products_for(
    conn: DbConn,
    *,
    category_slug: str | None = None,
    region_slug: str | None = None,
) -> list[ProductOut]:
    """Published products, filtered by category and/or region.

    Published only, and only from a published vendor — the join enforces what the
    publish gate already promises, so a draft vendor's product cannot surface even
    if its own status were wrong.
    """
    rows = await conn.fetch(
        """
        SELECT p.slug, p.name, p.summary, p.price_paise, p.unit,
               v.name AS vendor_name, v.slug AS vendor_slug, v.contact AS vendor_contact,
               r.name AS region_name,
               array_remove(array_agg(it.label), NULL) AS categories
        FROM products p
        JOIN vendors v ON v.id = p.vendor_id AND v.status = 'published'
        LEFT JOIN regions r ON r.id = p.region_id
        LEFT JOIN product_tags pt ON pt.product_id = p.id
        LEFT JOIN interest_tags it ON it.id = pt.tag_id
        WHERE p.status = 'published'
          AND ($1::text IS NULL OR EXISTS (
                  SELECT 1 FROM product_tags x
                  JOIN interest_tags t ON t.id = x.tag_id
                  WHERE x.product_id = p.id AND t.slug = $1
              ))
          AND ($2::text IS NULL OR r.slug = $2)
        GROUP BY p.slug, p.name, p.summary, p.price_paise, p.unit,
                 v.name, v.slug, v.contact, r.name
        ORDER BY p.name
        """,
        category_slug,
        region_slug,
    )
    return [
        ProductOut(
            slug=str(r["slug"]),
            name=str(r["name"]),
            summary=r["summary"],
            price_note=_rupees(r["price_paise"], r["unit"]),
            unit=r["unit"],
            vendor_name=str(r["vendor_name"]),
            vendor_slug=str(r["vendor_slug"]),
            vendor_contact=dict(r["vendor_contact"] or {}),
            region_name=r["region_name"],
            categories=[str(c) for c in (r["categories"] or [])],
        )
        for r in rows
    ]


def _rupees(paise: int | None, unit: str | None) -> str | None:
    """Integer paise to a display string. None stays None — "market rate" is real."""
    if paise is None:
        return None
    amount = f"₹{int(paise) // 100:,}"
    return f"{amount} / {unit}" if unit else amount


class MarketStats(BaseModel):
    """How much of the marketplace is real, for the page to state plainly."""

    specialities: int = 0
    categories: int = 0
    published_vendors: int = 0
    published_products: int = 0
    # Vendors we hold but refuse to show, so the number is not a mystery.
    withheld_vendors: int = 0


async def stats(conn: DbConn) -> MarketStats:
    row = await conn.fetchrow(
        """
        SELECT
            (SELECT count(*) FROM region_specialities) AS specialities,
            (SELECT count(DISTINCT tag_id) FROM region_specialities) AS categories,
            (SELECT count(*) FROM vendors WHERE status = 'published') AS published_vendors,
            (SELECT count(*) FROM products WHERE status = 'published') AS published_products,
            (SELECT count(*) FROM vendors WHERE status <> 'published') AS withheld_vendors
        """
    )
    assert row is not None
    return MarketStats(
        specialities=int(row["specialities"]),
        categories=int(row["categories"]),
        published_vendors=int(row["published_vendors"]),
        published_products=int(row["published_products"]),
        withheld_vendors=int(row["withheld_vendors"]),
    )


async def region_slugs_for_itinerary(conn: DbConn, itinerary_id: UUID) -> list[str]:
    """The regions an itinerary actually passes through.

    Read from `itinerary_pois` rather than the payload: that table exists precisely
    so "which places did this trip include" is answerable in SQL, and it means the
    take-home strip cannot drift from what was planned.
    """
    rows = await conn.fetch(
        """
        SELECT DISTINCT anc.slug
        FROM itinerary_pois ip
        JOIN pois p ON p.id = ip.poi_id
        JOIN regions r ON r.id = p.region_id
        -- Ancestors too, via the materialised path: a stop sits in a TALUK, and
        -- "this district grows arabica" is a district-level fact that still applies
        -- to a trip inside it. Matching only the exact region silently dropped the
        -- most useful row on every trip.
        JOIN regions anc ON r.path LIKE anc.path || '%'
        WHERE ip.itinerary_id = $1
          -- The state has no specialities and never should: "Karnataka grows
          -- coffee" is too coarse to act on.
          AND anc.kind <> 'state'
        """,
        itinerary_id,
    )
    return [str(r["slug"]) for r in rows]
