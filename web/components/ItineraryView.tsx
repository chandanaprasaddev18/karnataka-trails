"use client";

import { useState } from "react";
import Link from "next/link";
import { PhotoFrame, firstPhoto } from "@/components/PhotoFrame";
import { BookingRequest } from "@/components/BookingRequest";
import { RouteMap } from "@/components/RouteMap";
import { TakeHome } from "@/components/TakeHome";
import { formatDistance, formatDuration, formatMoney, titleCase } from "@/lib/format";
import type {
  Itinerary,
  ItineraryDay,
  ItineraryItem,
  ItineraryWarning,
  Photo,
} from "@/lib/types";

/**
 * The itinerary.
 *
 * Every number on screen is a field on the payload — no client-side arithmetic and
 * no re-derived totals, so what a reader sees cannot disagree with what was
 * stored. Paise are divided by 100 for display and nothing else.
 *
 * Laid out as the reference design does: the plan on the left, and a rail on the
 * right holding the day's route map and its timeline. The rail exists because
 * those two answer the questions people actually ask on the road — "what is next"
 * and "how far" — and they should not require scrolling past the prose.
 *
 * A client component only because of the day tabs; everything else is static.
 */
export function ItineraryView({
  itinerary,
  itineraryId,
}: {
  itinerary: Itinerary;
  /** From the API response; falls back to the payload's own id. */
  itineraryId?: string | null;
}) {
  const { summary, brief, days, return_leg: returnLeg } = itinerary;
  const [activeDay, setActiveDay] = useState<number>(days[0]?.day_number ?? 1);
  const current = days.find((d) => d.day_number === activeDay) ?? days[0];
  const heroPhoto = firstPhoto(brief.district.media) ?? firstDayPhoto(days);
  const measured = itinerary.days.some((d) => d.travel?.source === "osrm");

  return (
    <article className="mx-auto max-w-[1400px] px-4 py-5 sm:px-6 lg:px-8">
      {/* --- header ---------------------------------------------------------- */}
      <header className="relative overflow-hidden rounded-2xl border border-line">
        <div className="relative min-h-[260px]">
          <PhotoFrame
            photo={heroPhoto}
            alt={brief.district.name}
            tone="district"
            variant="cover"
            rounded="rounded-none"
            sizes="100vw"
            priority
            showCredit={false}
          />
          <div
            aria-hidden
            className="absolute inset-0 bg-gradient-to-r from-ink-950 via-ink-950/85 to-ink-950/25"
          />
          <div className="relative p-6 sm:p-8">
            <p className="eyebrow text-gold">
              {brief.days} {brief.days === 1 ? "day" : "days"}
              {brief.interests.length > 0 && ` · ${brief.interests.map((i) => i.label).join(" + ")}`}
              {brief.mode === "district" && brief.interests.length === 0 && " · whole district"}
              {brief.anchor && ` · around ${brief.anchor.label}`}
              {brief.radius_km && ` · ${brief.radius_km} km`}
            </p>
            <h1 className="mt-2 max-w-2xl font-display text-[28px] leading-tight font-bold sm:text-[36px]">
              {summary.title}
            </h1>
            <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-muted">
              <span>
                {brief.party_size} {brief.party_size === 1 ? "traveller" : "travellers"}
              </span>
              <Dot />
              <span>from {brief.origin.label}</span>
              <Dot />
              <span>{brief.district.name}</span>
            </p>
            {summary.narrative && (
              <p className="mt-4 max-w-2xl text-[14px] leading-relaxed text-cream/85">
                {summary.narrative}
              </p>
            )}

            <dl className="mt-6 grid max-w-2xl grid-cols-2 gap-2.5 sm:grid-cols-3">
              <Stat label="Total driving" value={formatDistance(summary.total_distance_km)} />
              <Stat
                label="Time on the road"
                value={formatDuration(summary.total_travel_minutes)}
                hint={measured ? "measured on real roads" : "estimated"}
              />
              <Stat
                label="Estimated cost"
                value={formatMoney(summary.estimated_cost)}
                hint="whole party · excl. transport"
              />
            </dl>
            {heroPhoto && (
              <p className="mt-4 text-[10px] text-muted-dim">
                Photo © {heroPhoto.artist} · {heroPhoto.license} ·{" "}
                <a
                  href={heroPhoto.source_page}
                  target="_blank"
                  rel="noopener noreferrer license"
                  className="underline"
                >
                  Wikimedia Commons
                </a>
              </p>
            )}
          </div>
        </div>
      </header>

      {summary.warnings.length > 0 && <Warnings warnings={summary.warnings} />}

      {/* --- day tabs -------------------------------------------------------- */}
      <div className="mt-5 flex flex-wrap items-center gap-2">
        {days.map((day) => (
          <Tab
            key={day.day_number}
            on={activeDay === day.day_number}
            onClick={() => setActiveDay(day.day_number)}
          >
            Day {day.day_number}
            {day.travel && (
              <span className="ml-2 font-mono text-[10.5px] opacity-70">
                {formatDistance(day.travel.distance_km)}
              </span>
            )}
          </Tab>
        ))}
      </div>

      <div className="mt-4 grid gap-5 lg:grid-cols-[1fr_360px]">
        {/* --- the day itself ------------------------------------------------ */}
        {/* min-w-0: a grid item defaults to min-width:auto, so the widest
            unbreakable child (here a stop card's photo row) sets the column width
            and pushes the page past the viewport on a phone. */}
        <section className="min-w-0">
          {current && (
            <DayBlock
              day={current}
              partySize={brief.party_size}
              itineraryId={itineraryId ?? null}
            />
          )}
        </section>

        {/* --- rail: map, then timeline -------------------------------------- */}
        <aside className="min-w-0 space-y-4 lg:sticky lg:top-5 lg:h-fit">
          {current && <RouteMap day={current} origin={brief.origin} />}
          {current && <Timeline day={current} />}
          {returnLeg && (
            <div className="panel p-4">
              <p className="eyebrow text-teal">Heading home</p>
              <p className="mt-1.5 text-[13px] text-cream/90">
                Back to {returnLeg.to.label} — {formatDistance(returnLeg.distance_km)},{" "}
                {formatDuration(returnLeg.duration_minutes)}.
              </p>
              <p className="mt-1 text-[10.5px] text-muted-dim">
                {returnLeg.source === "osrm" ? "Measured on the road network." : "Estimated."}
              </p>
            </div>
          )}
          {itineraryId && <TakeHome itineraryId={itineraryId} />}
          <Provenance itinerary={itinerary} />
        </aside>
      </div>

      <footer className="mt-8 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-5 text-[11.5px] text-muted-dim">
        <span>
          Composed by{" "}
          {itinerary.composer === "llm"
            ? `a language model${itinerary.llm_model ? ` (${itinerary.llm_model})` : ""}`
            : "our routing engine"}
        </span>
        <Dot />
        <span>schema v{itinerary.schema_version}</span>
        <Dot />
        <Link href="/plan" className="underline hover:text-gold">
          Plan another trip
        </Link>
      </footer>
    </article>
  );
}

/**
 * The compact hour-by-hour rail, as the reference shows it.
 *
 * Deliberately a summary and not a second copy of the day: times, names and a
 * thumbnail. Anything factual a reader might act on — cost, permit, guide — stays
 * on the full card, so there is one place to read it and no chance of the two
 * disagreeing.
 */
function Timeline({ day }: { day: ItineraryDay }) {
  if (day.items.length === 0 && !day.stay) return null;

  return (
    <div className="panel p-4">
      <p className="eyebrow text-gold">Day {day.day_number} timeline</p>
      <ol className="mt-3 space-y-3">
        {day.items.map((item) => {
          const photo = firstPhoto(item.media) ?? firstPhoto(item.region?.media);
          return (
            <li key={`${item.slot}-${item.poi_id}`} className="flex items-center gap-3">
              <span className="w-11 shrink-0 font-mono text-[10.5px] text-muted-dim">
                {item.start_time_estimate ?? "—"}
              </span>
              <span aria-hidden className="relative flex h-full items-center">
                <span className="h-2.5 w-2.5 rounded-full bg-gold" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[12.5px] font-medium">{item.name}</span>
                <span className="block truncate text-[10.5px] text-muted-dim">
                  {item.duration_minutes ? formatDuration(item.duration_minutes) : titleCase(item.kind)}
                </span>
              </span>
              <span className="relative h-10 w-14 shrink-0 overflow-hidden rounded-lg">
                <PhotoFrame
                  photo={photo}
                  alt=""
                  tone={item.kind === "activity" ? "activity" : "place"}
                  variant="cover"
                  rounded="rounded-none"
                  sizes="56px"
                  showCredit={false}
                />
              </span>
            </li>
          );
        })}
        {day.stay && (
          <li className="flex items-center gap-3 border-t border-line pt-3">
            <span className="w-11 shrink-0 font-mono text-[10.5px] text-muted-dim">night</span>
            <span aria-hidden className="flex h-full items-center">
              <span className="h-2.5 w-2.5 rounded-full bg-teal" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[12.5px] font-medium">{day.stay.name}</span>
              <span className="block truncate text-[10.5px] text-muted-dim">
                {titleCase(day.stay.stay_type)}
              </span>
            </span>
          </li>
        )}
      </ol>
    </div>
  );
}

/**
 * How trustworthy this particular itinerary is, in the rail.
 *
 * `composer` and the leg sources are already in the payload; surfacing them means
 * a reader can tell a measured plan from an estimated one without reading the
 * warnings, and a fallback plan cannot quietly pass for a curated one.
 */
function Provenance({ itinerary }: { itinerary: Itinerary }) {
  const sources = new Set(
    itinerary.days.flatMap((d) => [
      d.travel?.source,
      ...d.items.map((i) => i.leg_from_previous?.source),
    ]),
  );
  const estimated = sources.has("static_haversine");
  const measured = sources.has("osrm");

  return (
    <div className="panel p-4">
      <p className="eyebrow text-muted-dim">How this was built</p>
      <ul className="mt-2 space-y-1.5 text-[11.5px] leading-snug text-muted-dim">
        <li>
          Stops:{" "}
          <span className="text-cream/80">
            chosen from our curated database, never invented
          </span>
        </li>
        <li>
          Driving:{" "}
          <span className="text-cream/80">
            {measured && !estimated
              ? "measured on the real road network"
              : measured
                ? "measured, with some legs estimated"
                : "estimated from straight-line distance"}
          </span>
        </li>
        <li>
          Pacing:{" "}
          <span className="text-cream/80">
            {itinerary.composer === "llm" ? "curated by a language model" : "greedy by proximity"}
          </span>
        </li>
      </ul>
    </div>
  );
}

function DayBlock({
  day,
  partySize,
  itineraryId,
}: {
  day: ItineraryDay;
  partySize: number;
  itineraryId: string | null;
}) {
  return (
    <section>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-[20px] font-bold">
          <span className="text-muted-dim">Day {day.day_number}</span>
          <span aria-hidden className="mx-2 text-line">
            /
          </span>
          {stripDayPrefix(day.title, day.day_number)}
        </h2>
        {day.travel && (
          <span className="eyebrow text-muted-dim">
            {formatDistance(day.travel.distance_km)} ·{" "}
            {formatDuration(day.travel.duration_minutes)} driving
          </span>
        )}
      </div>

      {day.narrative && (
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-muted">{day.narrative}</p>
      )}

      {day.items.length === 0 ? (
        <p className="mt-4 rounded-xl border border-dashed border-line bg-ink-850 px-4 py-5 text-[13px] text-muted">
          No stops today — the day is taken up by the drive.
        </p>
      ) : (
        <ol className="mt-4 space-y-3">
          {day.items.map((item) => (
            <li key={`${item.slot}-${item.poi_id}`}>
              <Stop item={item} />
            </li>
          ))}
        </ol>
      )}

      <StayRow day={day} partySize={partySize} itineraryId={itineraryId} />
    </section>
  );
}

function Stop({ item }: { item: ItineraryItem }) {
  const permit = item.detail?.requires_permit === true;
  // Its own photograph if it has one, else its taluk's. Activities and stays
  // never get their own — see the fetcher's rules — so the locality stands in.
  const own = firstPhoto(item.media);
  const photo = own ?? firstPhoto(item.region?.media);
  const extra = item.media ? Math.max(0, item.media.length - 1) : 0;

  return (
    <div>
      <LegLine leg={item.leg_from_previous} />
      <div className="panel flex flex-col gap-3 p-3.5 sm:flex-row sm:gap-4">
        <div className="relative shrink-0">
          <PhotoFrame
            photo={photo}
            alt={item.name}
            tone={item.kind === "activity" ? "activity" : "place"}
            rounded="rounded-lg"
            className="h-40 w-full sm:h-28 sm:w-40"
            sizes="(max-width: 640px) 92vw, 160px"
            showCredit={false}
          />
          {extra > 0 && (
            <span className="absolute right-1.5 bottom-1.5 rounded-full bg-ink-950/85 px-1.5 py-0.5 font-mono text-[9.5px] text-cream/90">
              +{extra}
            </span>
          )}
        </div>
        <div className="min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-mono text-[11px] text-gold">
              {item.start_time_estimate ?? "—"}
            </span>
            <h3 className="font-display text-[15.5px] font-semibold">{item.name}</h3>
            {item.kind === "activity" && (
              <span className="eyebrow text-teal">activity</span>
            )}
          </div>
          <p className="text-[12.5px] leading-snug text-muted">{item.summary}</p>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-muted-dim">
            {item.duration_minutes && <span>{formatDuration(item.duration_minutes)}</span>}
            {item.cost && (
              <>
                <Dot />
                <span>{formatMoney(item.cost)} pp</span>
              </>
            )}
            {permit && (
              <span className="rounded-full border border-rust px-2 py-0.5 font-mono text-[9.5px] tracking-wide text-rust uppercase">
                permit required
              </span>
            )}
          </p>
          {item.why_chosen && (
            <p className="border-l-2 border-gold/50 pl-2.5 text-[12.5px] text-muted italic">
              {item.why_chosen}
            </p>
          )}
          {item.guides.length > 0 && (
            <p className="text-[11.5px] text-teal">
              Guide:{" "}
              {item.guides
                .map((g) => `${g.name}${g.languages.length ? ` (${g.languages.join("/")})` : ""}`)
                .join(", ")}
            </p>
          )}
          {photo && (
            <p className="truncate text-[10px] text-muted-dim/80">
              Photo © {photo.artist} · {photo.license}
              {!own && " · shows the surrounding area"}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function StayRow({
  day,
  partySize,
  itineraryId,
}: {
  day: ItineraryDay;
  partySize: number;
  itineraryId: string | null;
}) {
  if (!day.stay) {
    return (
      <p className="mt-3 rounded-xl border border-line bg-ink-850 px-4 py-3 text-[12.5px] text-muted">
        No stay selected for tonight — you will need to arrange your own.
      </p>
    );
  }

  // A stay is a private property and never has a Commons photograph of its own,
  // so this is its locality — labelled as such, because a reader must not think
  // it is a picture of the room.
  const area = firstPhoto(day.stay.region?.media);

  return (
    <div className="panel-raised mt-3 flex gap-3.5 overflow-hidden p-3.5">
      {area && (
        <div className="relative h-16 w-20 shrink-0 sm:h-20 sm:w-28">
          <PhotoFrame
            photo={area}
            alt={`Around ${day.stay.region?.name ?? day.stay.name}`}
            tone="stay"
            variant="cover"
            rounded="rounded-lg"
            sizes="112px"
            showCredit={false}
          />
        </div>
      )}
      <div className="min-w-0">
        <p className="eyebrow text-gold">Tonight</p>
        <p className="mt-1 text-[13.5px]">
          <span className="font-medium">{day.stay.name}</span>
          <span className="text-muted"> · {titleCase(day.stay.stay_type)}</span>
          {day.stay.per_night && (
            <span className="text-muted"> · {formatMoney(day.stay.per_night)}/night</span>
          )}
          {day.stay.meals_included && <span className="text-teal"> · meals included</span>}
        </p>
        {day.stay.amenities.length > 0 && (
          <p className="mt-0.5 text-[11.5px] text-muted-dim">
            {day.stay.amenities.map((a) => titleCase(a)).join(" · ")}
          </p>
        )}
        {area && (
          <p className="mt-1 truncate text-[10px] text-muted-dim/80">
            Photo shows {day.stay.region?.name ?? "the area"} · © {area.artist} · {area.license}
          </p>
        )}
        {/* Phase 4. The panel is explicit that this records intent rather than
            booking anything — we hold no verified contact for any stay. */}
        <BookingRequest
          kind="stay"
          slug={day.stay.slug}
          name={day.stay.name}
          partySize={partySize}
          itineraryId={itineraryId}
          dayNumber={day.day_number}
        />
      </div>
    </div>
  );
}

/**
 * The hop from the previous stop.
 *
 * Suppressed when there is effectively no journey: two shrines in the same
 * complex are 60 m apart, which rounds to "0 km" with a blank duration and reads
 * as broken rather than as "it is right there".
 */
function LegLine({ leg }: { leg: ItineraryItem["leg_from_previous"] }) {
  if (!leg) return null;
  if (leg.duration_minutes === 0 && leg.distance_km < 1) {
    return <p className="mb-1.5 font-mono text-[10.5px] text-muted-dim">↓ a few steps away</p>;
  }
  const parts = [formatDistance(leg.distance_km), formatDuration(leg.duration_minutes)].filter(
    Boolean,
  );
  return (
    <p className="mb-1.5 font-mono text-[10.5px] text-muted-dim">
      ↓ {parts.join(" · ")}
      {leg.source === "static_haversine" && " · estimated"}
    </p>
  );
}

/**
 * Warnings sit above the days, not below them.
 *
 * The data is hand-compiled and some of it is unverified, so an itinerary that is
 * stretching or unconfirmed has to say so where the reader will actually see it —
 * before they start reading the plan, not after.
 */
function Warnings({ warnings }: { warnings: ItineraryWarning[] }) {
  return (
    <section className="mt-4 rounded-2xl border border-rust/35 bg-rust-soft p-5">
      <p className="eyebrow text-rust">Before you go</p>
      <ul className="mt-2.5 grid gap-2 text-[12.5px] text-cream/85 sm:grid-cols-2">
        {warnings.map((warning, index) => (
          <li key={`${warning.code}-${index}`} className="flex gap-2">
            <span aria-hidden className="text-rust">
              •
            </span>
            <span>
              {warning.day_number && (
                <span className="font-medium">Day {warning.day_number}: </span>
              )}
              {warning.message}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Tab({
  on,
  onClick,
  children,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={`rounded-full px-4 py-1.5 font-mono text-[12px] transition ${
        on
          ? "bg-gold text-ink-950"
          : "border border-line bg-ink-850 text-muted hover:border-gold/50"
      }`}
    >
      {children}
    </button>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-line bg-ink-850/80 px-3.5 py-2.5">
      <dt className="eyebrow text-muted-dim">{label}</dt>
      <dd className="mt-0.5 font-display text-[17px] font-semibold">{value}</dd>
      {hint && <dd className="text-[10px] text-muted-dim">{hint}</dd>}
    </div>
  );
}

function Dot() {
  return (
    <span aria-hidden className="text-line">
      ·
    </span>
  );
}

function firstDayPhoto(days: ItineraryDay[]): Photo | null {
  for (const day of days) {
    for (const item of day.items) {
      const photo = firstPhoto(item.media) ?? firstPhoto(item.region?.media);
      if (photo) return photo;
    }
  }
  return null;
}

/** The composer prefixes its titles with "Day N — "; the heading already shows that. */
function stripDayPrefix(title: string, dayNumber: number): string {
  return title.replace(new RegExp(`^Day\\s*${dayNumber}\\s*[—–-]\\s*`), "");
}
