"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { fetchAnchors } from "@/lib/api";
import type { Anchor } from "@/lib/types";

/**
 * The global search, as the reference design has it at the top of every screen.
 *
 * It searches ONE thing — the anchors we hold, meaning published places and the
 * regions they sit in — and hands off to location mode with that anchor selected.
 * The reference placeholder reads "Search destinations, places, experiences…";
 * ours says places and towns, because experiences are not built and a search box
 * that silently returns nothing for a third of what it advertises is worse than a
 * narrower promise.
 *
 * Results show how much is near each hit, so "nothing to plan there" is visible
 * before the click rather than after a generated trip turns out thin.
 */
export function SearchBar() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Anchor[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  // "Too short to search" is a fact about the current query, not a state change:
  // computing it during render avoids a setState-inside-effect cascade, and the
  // list can never lag a keystroke behind the box.
  const tooShort = query.trim().length < 2;
  const visible = tooShort ? [] : results;

  useEffect(() => {
    if (tooShort) return;
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      fetchAnchors(query)
        .then((rows) => {
          setResults(rows.slice(0, 6));
          setActive(0);
        })
        .catch(() => setResults([]));
    }, 200);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [query, tooShort]);

  // Clicking anywhere else closes the panel. Without this it stays open behind
  // the next thing the user does, which reads as a stuck dropdown.
  useEffect(() => {
    function onDocumentClick(event: MouseEvent) {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentClick);
    return () => document.removeEventListener("mousedown", onDocumentClick);
  }, []);

  function go(anchor: Anchor) {
    setOpen(false);
    setQuery("");
    router.push(`/plan/location?anchor=${encodeURIComponent(anchor.slug)}`);
  }

  return (
    <div ref={box} className="relative w-full max-w-xl">
      <input
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(event) => {
          if (!visible.length) return;
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setActive((i) => (i + 1) % visible.length);
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActive((i) => (i - 1 + visible.length) % visible.length);
          } else if (event.key === "Enter") {
            event.preventDefault();
            go(visible[active]);
          } else if (event.key === "Escape") {
            setOpen(false);
          }
        }}
        placeholder="Search a place or town — Mullayanagiri, Mudigere, Kalasa…"
        aria-label="Search places and towns"
        className="w-full rounded-xl border border-line bg-ink-850 py-2.5 pr-3 pl-9 text-[13.5px] text-cream placeholder:text-muted-dim"
      />
      <span
        aria-hidden
        className="pointer-events-none absolute top-2.5 left-3 text-[13px] text-muted-dim"
      >
        ⌕
      </span>

      {open && !tooShort && (
        <div className="absolute top-12 right-0 left-0 z-20 overflow-hidden rounded-xl border border-line bg-ink-900 shadow-2xl">
          {visible.length === 0 ? (
            <p className="px-4 py-3 text-[12.5px] text-muted-dim">
              Nothing by that name in our data yet. Only Chikkamagaluru is seeded so far.
            </p>
          ) : (
            <ul>
              {visible.map((anchor, index) => (
                <li key={`${anchor.kind}-${anchor.slug}`}>
                  <button
                    type="button"
                    onMouseEnter={() => setActive(index)}
                    onClick={() => go(anchor)}
                    className={`flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition ${
                      index === active ? "bg-ink-800" : ""
                    }`}
                  >
                    <span
                      aria-hidden
                      className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[11px] ${
                        anchor.kind === "region"
                          ? "bg-ink-700 text-cream"
                          : "bg-teal-soft text-teal"
                      }`}
                    >
                      {anchor.kind === "region" ? "▣" : "◉"}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px]">{anchor.label}</span>
                      <span className="block truncate text-[10.5px] text-muted-dim">
                        {anchor.sublabel}
                      </span>
                    </span>
                    <span className="shrink-0 font-mono text-[10px] text-muted-dim">
                      {anchor.nearby} near
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
