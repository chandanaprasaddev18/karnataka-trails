"use client";

import { useState } from "react";
import Link from "next/link";
import { DuplicateBookingError, createBooking } from "@/lib/api";
import type { BookingKind } from "@/lib/types";

/**
 * "Request this stay" — and an honest account of what that does.
 *
 * WHAT IT DOES NOT DO: book anything. We hold no verified contact for any stay,
 * every seeded guide is a placeholder, and there is no partner API or payment
 * provider. So the button records the request against this browser's session and
 * says, in the panel, that we could not send it anywhere.
 *
 * That is not a placeholder for a better flow — it is the correct flow for the data
 * we have. The alternative designs are worse: a disabled button that explains
 * nothing, or a "Booked!" toast that is a lie.
 *
 * The success state therefore leads with what the traveller must do next, not with
 * a tick.
 */
export function BookingRequest({
  kind,
  slug,
  name,
  partySize,
  itineraryId,
  dayNumber,
}: {
  kind: BookingKind;
  slug: string;
  name: string;
  partySize: number;
  itineraryId?: string | null;
  dayNumber?: number | null;
}) {
  const [open, setOpen] = useState(false);
  const [party, setParty] = useState(partySize);
  const [checkIn, setCheckIn] = useState("");
  const [checkOut, setCheckOut] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [duplicate, setDuplicate] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await createBooking({
        kind,
        slug,
        party_size: party,
        check_in: checkIn || null,
        check_out: checkOut || null,
        note: note || null,
        itinerary_id: itineraryId ?? null,
        day_number: dayNumber ?? null,
      });
      setDone(true);
    } catch (caught: unknown) {
      if (caught instanceof DuplicateBookingError) {
        setDuplicate(true);
        setDone(true);
      } else {
        setError(caught instanceof Error ? caught.message : "Could not record that.");
      }
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="mt-2 rounded-xl border border-teal/30 bg-teal-soft p-3">
        <p className="text-[12.5px] text-cream">
          {duplicate ? "Already on your list." : "Saved to your requests."}{" "}
          <Link href="/bookings" className="underline hover:text-gold">
            See your requests
          </Link>
        </p>
        <p className="mt-1 text-[11px] leading-snug text-muted">
          We have <span className="text-cream">not</span> sent this to the property — we hold
          no verified contact for it, so you will need to reach them yourself.
        </p>
      </div>
    );
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-2 rounded-lg border border-line px-3 py-1.5 text-[12px] text-muted transition hover:border-gold hover:text-gold"
      >
        Request {kind === "stay" ? "this stay" : kind === "guide" ? "this guide" : "this activity"}
      </button>
    );
  }

  return (
    <div className="mt-2 space-y-3 rounded-xl border border-line bg-ink-900 p-3.5">
      <div>
        <p className="eyebrow text-gold">Record a request</p>
        <p className="mt-1 text-[12px] leading-snug text-muted">
          This saves what you want for <span className="text-cream">{name}</span>. It does not
          contact anyone: we do not hold a verified phone number or email for it, and we will
          not invent one.
        </p>
      </div>

      <div className="grid gap-2.5 sm:grid-cols-3">
        <label className="block">
          <span className="eyebrow block text-muted-dim">Travellers</span>
          <input
            type="number"
            min={1}
            max={30}
            value={party}
            onChange={(event) => setParty(Number(event.target.value))}
            className="mt-1 w-full rounded-lg border border-line bg-ink-850 px-2.5 py-1.5 text-[13px] text-cream"
          />
        </label>
        <label className="block">
          <span className="eyebrow block text-muted-dim">
            {kind === "stay" ? "Check in" : "From"}
          </span>
          <input
            type="date"
            value={checkIn}
            onChange={(event) => setCheckIn(event.target.value)}
            className="mt-1 w-full rounded-lg border border-line bg-ink-850 px-2.5 py-1.5 text-[13px] text-cream"
          />
        </label>
        <label className="block">
          <span className="eyebrow block text-muted-dim">
            {kind === "stay" ? "Check out" : "To"}
          </span>
          <input
            type="date"
            value={checkOut}
            onChange={(event) => setCheckOut(event.target.value)}
            className="mt-1 w-full rounded-lg border border-line bg-ink-850 px-2.5 py-1.5 text-[13px] text-cream"
          />
        </label>
      </div>

      <label className="block">
        <span className="eyebrow block text-muted-dim">Anything they should know</span>
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          rows={2}
          maxLength={500}
          placeholder="Late arrival, dietary needs, two rooms…"
          className="mt-1 w-full rounded-lg border border-line bg-ink-850 px-2.5 py-1.5 text-[13px] text-cream placeholder:text-muted-dim"
        />
      </label>

      {error && <p className="text-[12px] text-rust">{error}</p>}

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={busy}
          className="rounded-lg bg-gold px-3.5 py-2 font-display text-[13px] font-semibold text-ink-950 transition hover:brightness-110 disabled:opacity-60"
        >
          {busy ? "Saving…" : "Save request"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded-lg border border-line px-3 py-2 text-[12.5px] text-muted transition hover:text-cream"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
