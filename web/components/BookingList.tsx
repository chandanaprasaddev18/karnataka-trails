"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { fetchBookings, withdrawBooking } from "@/lib/api";
import type { Booking } from "@/lib/types";

/**
 * The traveller's saved requests.
 *
 * Scoped by the session token in localStorage: there are no accounts, so this
 * browser is the identity. That is stated on the page, because a list of your
 * bookings that silently disappears when you switch device is worse than one that
 * warned you it would.
 *
 * The status column is where honesty lives. Every row this release can produce is
 * `requested` with `deliverable: false`, so each one says we could not send it and
 * what to do instead. Nothing here ever reads "confirmed" — the API cannot write
 * that without a real channel, and the database refuses it too.
 */
export function BookingList() {
  const [bookings, setBookings] = useState<Booking[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchBookings()
      .then(setBookings)
      .catch(() => setError("Could not reach the API. Start it with `make api`."));
  }, []);

  useEffect(load, [load]);

  async function drop(id: string) {
    setBusy(id);
    try {
      await withdrawBooking(id);
      load();
    } finally {
      setBusy(null);
    }
  }

  const open = (bookings ?? []).filter((b) => b.status !== "withdrawn");
  const closed = (bookings ?? []).filter((b) => b.status === "withdrawn");

  return (
    <div className="mx-auto max-w-[1000px] px-4 py-6 sm:px-6 lg:px-8">
      <p className="eyebrow text-gold">My requests</p>
      <h1 className="mt-2 font-display text-[30px] font-bold">Stays and guides you asked about</h1>
      <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-muted">
        Saved to this browser — there are no accounts yet, so these will not follow you to
        another device.
      </p>

      <div className="mt-5 rounded-xl border border-rust/30 bg-rust-soft p-4">
        <p className="eyebrow text-rust">What a request is, and is not</p>
        <p className="mt-1.5 text-[12.5px] leading-relaxed text-cream/85">
          We record what you want; we do not send it. No stay in our data has a verified phone
          number or email, every guide in it is a placeholder, and we will not invent contact
          details to fill the gap. Take the details below and reach the property yourself.
        </p>
      </div>

      {error && (
        <p className="mt-5 rounded-xl border border-rust/40 bg-rust-soft p-4 text-[13px] text-rust">
          {error}
        </p>
      )}

      {bookings === null && !error && (
        <div className="mt-5 space-y-3">
          {[0, 1].map((row) => (
            <div key={row} className="panel h-24 animate-pulse" />
          ))}
        </div>
      )}

      {bookings !== null && bookings.length === 0 && (
        <div className="panel mt-5 p-6">
          <p className="font-display text-[16px] font-semibold">Nothing saved yet</p>
          <p className="mt-1.5 text-[13px] text-muted">
            Plan a trip, then use “Request this stay” on any night of it.
          </p>
          <Link
            href="/plan"
            className="mt-4 inline-block rounded-xl bg-gold px-4 py-2.5 font-display text-[13.5px] font-semibold text-ink-950 transition hover:brightness-110"
          >
            Plan a trip →
          </Link>
        </div>
      )}

      {open.length > 0 && (
        <ul className="mt-5 space-y-3">
          {open.map((booking) => (
            <li key={booking.id}>
              <BookingCard
                booking={booking}
                onWithdraw={() => drop(booking.id)}
                busy={busy === booking.id}
              />
            </li>
          ))}
        </ul>
      )}

      {closed.length > 0 && (
        <>
          <p className="eyebrow mt-8 text-muted-dim">Withdrawn</p>
          <ul className="mt-2.5 space-y-2">
            {closed.map((booking) => (
              <li
                key={booking.id}
                className="flex items-center justify-between gap-3 rounded-xl border border-line px-4 py-2.5 text-[12.5px] text-muted-dim"
              >
                <span className="truncate">{booking.target.name}</span>
                <span className="eyebrow shrink-0">withdrawn</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function BookingCard({
  booking,
  onWithdraw,
  busy,
}: {
  booking: Booking;
  onWithdraw: () => void;
  busy: boolean;
}) {
  const { target } = booking;
  const dates =
    booking.check_in && booking.check_out
      ? `${booking.check_in} → ${booking.check_out}`
      : (booking.check_in ?? "no dates given");

  return (
    <div className="panel p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="eyebrow text-muted-dim">
            {target.kind}
            {target.locality ? ` · ${target.locality}` : ""}
          </p>
          <h2 className="mt-0.5 font-display text-[16px] font-semibold">{target.name}</h2>
          <p className="mt-1 text-[12.5px] text-muted">
            {booking.party_size} {booking.party_size === 1 ? "traveller" : "travellers"} ·{" "}
            {dates}
            {target.price_note ? ` · ${target.price_note}` : ""}
          </p>
          {booking.note && (
            <p className="mt-1.5 border-l-2 border-gold/40 pl-2.5 text-[12.5px] text-muted italic">
              {booking.note}
            </p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <StatusChip booking={booking} />
          <button
            type="button"
            onClick={onWithdraw}
            disabled={busy}
            className="rounded-lg border border-line px-2.5 py-1.5 text-[11.5px] text-muted-dim transition hover:border-rust hover:text-rust disabled:opacity-50"
          >
            {busy ? "…" : "Withdraw"}
          </button>
        </div>
      </div>

      {/* The whole point of the card: what the traveller has to do, because we
          could not do it for them. */}
      <div className="mt-3 border-t border-line pt-3">
        {booking.deliverable ? (
          <p className="text-[12px] text-teal">
            Contact on file:{" "}
            <span className="text-cream">{JSON.stringify(target.contact)}</span>
          </p>
        ) : (
          <p className="text-[12px] leading-snug text-muted-dim">
            <span className="text-rust">Not sent.</span> We hold no verified contact for this
            {target.kind === "guide" ? " guide" : " property"}
            {target.locality ? `, so ask locally in ${target.locality}` : ""} or search for it by
            name. {target.is_placeholder && "This record is a development placeholder."}
          </p>
        )}
        {booking.itinerary_id && (
          <Link
            href={`/itinerary/${booking.itinerary_id}`}
            className="mt-2 inline-block text-[12px] text-muted underline hover:text-gold"
          >
            Back to the trip {booking.day_number ? `(day ${booking.day_number})` : ""}
          </Link>
        )}
      </div>
    </div>
  );
}

/**
 * The status, styled by who is waiting on whom.
 *
 * `requested` is deliberately not green: nothing is in motion, and a colour that
 * suggests progress would undo everything the copy is careful about.
 */
function StatusChip({ booking }: { booking: Booking }) {
  const tone =
    booking.status === "confirmed"
      ? "border-teal text-teal"
      : booking.status === "declined"
        ? "border-rust text-rust"
        : "border-line text-muted-dim";
  const label = booking.status === "requested" ? "saved, not sent" : booking.status;
  return (
    <span
      className={`rounded-full border px-2.5 py-1 font-mono text-[10px] tracking-wide uppercase ${tone}`}
    >
      {label}
    </span>
  );
}
