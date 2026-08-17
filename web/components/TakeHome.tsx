"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchSpecialities } from "@/lib/api";
import type { Speciality } from "@/lib/types";

/**
 * "Take home" — what the places on THIS trip produce.
 *
 * Scoped by itinerary id, and the server derives the regions from
 * `itinerary_pois` rather than from the payload, so the strip cannot drift from
 * the stops that were actually planned.
 *
 * No prices and no sellers: a speciality is a fact about a place, and we list no
 * traders. The marketplace page explains why; this strip just has to be useful
 * while someone is standing there.
 */
export function TakeHome({ itineraryId }: { itineraryId: string }) {
  const [rows, setRows] = useState<Speciality[] | null>(null);

  useEffect(() => {
    fetchSpecialities({ itineraryId })
      .then((view) => setRows(view.specialities))
      .catch(() => setRows([]));
  }, [itineraryId]);

  if (!rows || rows.length === 0) return null;

  return (
    <div className="panel p-4">
      <div className="flex items-baseline justify-between gap-2">
        <p className="eyebrow text-gold">Take home</p>
        <Link href="/marketplace" className="eyebrow text-muted-dim hover:text-gold">
          all →
        </Link>
      </div>
      <ul className="mt-2.5 space-y-2.5">
        {rows.slice(0, 4).map((row) => (
          <li key={`${row.region_slug}-${row.category_slug}`}>
            <p className="text-[12.5px] font-medium">
              {row.category_label}
              <span className="text-muted-dim"> · {row.region_name}</span>
            </p>
            <p className="mt-0.5 text-[11.5px] leading-snug text-muted-dim">{row.note}</p>
          </li>
        ))}
      </ul>
      <p className="mt-3 border-t border-line pt-2.5 text-[10.5px] leading-snug text-muted-dim">
        What the area produces. We list no sellers — ask locally, or at the estate shops.
      </p>
    </div>
  );
}
