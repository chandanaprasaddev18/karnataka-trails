"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { SearchBar } from "@/components/SearchBar";

/**
 * The application shell: a fixed left rail on desktop, a drawer on mobile.
 *
 * The rail lists the whole product, including the parts that do not exist yet,
 * because a roadmap the user can see beats a surprise. Anything unbuilt is
 * rendered as text with the phase it belongs to — never as a link that 404s, and
 * never as a link that silently does nothing. That rule already applied to the
 * old top nav; here it applies to a dozen more items.
 *
 * A client component only for the mobile drawer and the active-route highlight.
 */

type Item = {
  label: string;
  href?: string;
  glyph: string;
  /** Set when the item is not built. The value is what the user is told. */
  soon?: string;
};

type Group = { heading: string | null; items: Item[] };

const NAV: Group[] = [
  { heading: null, items: [{ label: "Home", href: "/", glyph: "◆" }] },
  {
    heading: "Plan a trip",
    items: [
      { label: "By interest", href: "/plan", glyph: "◐" },
      { label: "By location", href: "/plan/location", glyph: "◇" },
      { label: "By district", href: "/plan/district", glyph: "▵" },
    ],
  },
  {
    heading: "Explore",
    items: [
      { label: "Districts", href: "/#districts", glyph: "◈" },
      { label: "Places", href: "/#gallery", glyph: "◉" },
      { label: "Experiences", glyph: "✦", soon: "needs operator data" },
      { label: "Hidden gems", glyph: "✧", soon: "needs more seed districts" },
    ],
  },
  {
    heading: "My stuff",
    items: [
      { label: "My trips", glyph: "≡", soon: "needs accounts to survive a device change" },
      { label: "Saved", glyph: "♡", soon: "needs accounts" },
      // Live as of Phase 4. It lists REQUESTS, not confirmed bookings — the page
      // itself is explicit about the difference.
      { label: "My requests", href: "/bookings", glyph: "▤" },
    ],
  },
  {
    heading: "Other",
    items: [
      { label: "Guides", glyph: "☗", soon: "every seeded guide is a placeholder" },
      // Live as of Phase 5. Lists what districts produce; lists no sellers, and
      // says why on the page.
      { label: "Marketplace", href: "/marketplace", glyph: "▣" },
    ],
  },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="min-h-screen bg-ink-950 lg:flex">
      {/* Mobile bar. The rail is 240px of a 390px screen, so it becomes a drawer. */}
      <div className="flex items-center justify-between border-b border-line px-4 py-3 lg:hidden">
        <Brand />
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label="Toggle navigation"
          className="rounded-lg border border-line px-3 py-1.5 font-mono text-[12px] text-muted"
        >
          {open ? "✕" : "☰"}
        </button>
      </div>

      <aside
        className={`${
          open ? "block" : "hidden"
        } shrink-0 border-line bg-ink-900 lg:sticky lg:top-0 lg:block lg:h-screen lg:w-[248px] lg:border-r`}
      >
        <div className="hidden px-5 pt-6 pb-2 lg:block">
          <Brand />
          <p className="mt-1.5 text-[11.5px] text-muted-dim">
            Karnataka, planned from real data.
          </p>
        </div>
        <nav className="thin-scroll space-y-5 overflow-y-auto px-3 py-4 lg:h-[calc(100vh-6.5rem)]">
          {NAV.map((group) => (
            <div key={group.heading ?? "top"}>
              {group.heading && (
                <p className="eyebrow px-2 pb-1.5 text-muted-dim">{group.heading}</p>
              )}
              <ul className="space-y-0.5">
                {group.items.map((item) => (
                  <li key={item.label}>
                    <NavRow item={item} onNavigate={() => setOpen(false)} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <Provenance />
        </nav>
      </aside>

      <div className="min-w-0 flex-1">
        {/* The search sits above the content rather than inside the rail: it is
            about the main column, and at 248px the rail cannot hold a usable
            input. Hidden on mobile, where the drawer button owns that row. */}
        <div className="hidden border-b border-line px-4 py-3 sm:px-6 lg:block lg:px-8">
          <SearchBar />
        </div>
        {children}
      </div>
    </div>
  );
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2">
      <span
        aria-hidden
        className="flex h-7 w-7 items-center justify-center rounded-lg bg-gold font-display text-[15px] font-bold text-ink-950"
      >
        ᵏ
      </span>
      <span className="font-display text-[19px] font-bold tracking-tight text-gold">
        Karnataka Trails
      </span>
    </Link>
  );
}

function NavRow({ item, onNavigate }: { item: Item; onNavigate: () => void }) {
  const pathname = usePathname();
  /*
   * Exact match, and never for a hash link.
   *
   * Prefix matching lit up "By interest" (/plan) while on /plan/location, because
   * the location wizard lives under it. And the in-page hash links (/#districts)
   * share the home pathname, so they would all highlight together on the home
   * page. Every real route in this rail is a leaf, so equality is the whole rule.
   */
  const active = !!item.href && !item.href.includes("#") && pathname === item.href;

  const inner = (
    <>
      <span aria-hidden className="w-4 text-center text-[13px]">
        {item.glyph}
      </span>
      <span className="flex-1 truncate">{item.label}</span>
    </>
  );

  if (!item.href) {
    return (
      <span
        title={`Not built yet: ${item.soon}`}
        className="flex cursor-not-allowed items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] text-muted-dim/70"
      >
        {inner}
        <span className="eyebrow shrink-0 text-[8.5px] text-muted-dim/60">soon</span>
      </span>
    );
  }

  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] transition ${
        active
          ? "bg-gold-soft text-gold ring-1 ring-gold/30"
          : "text-muted hover:bg-ink-800 hover:text-cream"
      }`}
    >
      {inner}
    </Link>
  );
}

/**
 * Where the data comes from, in the rail rather than buried in a footer.
 *
 * This is the one claim the whole product rests on, so it is visible on every
 * screen: the places are ours and curated, the driving is measured, the photos
 * belong to other people and are credited.
 */
function Provenance() {
  return (
    <div className="mx-1 mt-2 rounded-xl border border-line bg-ink-850 p-3">
      <p className="eyebrow text-gold">Where this comes from</p>
      <ul className="mt-2 space-y-1.5 text-[11px] leading-snug text-muted-dim">
        <li>Places from our own curated database — nothing invented.</li>
        <li>Driving measured on the real road network (OSRM).</li>
        <li>Photographs from Wikimedia Commons, credited on each image.</li>
      </ul>
    </div>
  );
}
