"use client";

import { useState } from "react";
import Link from "next/link";
import { PhotoFrame, firstPhoto } from "@/components/PhotoFrame";
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
 * A client component only because of the day tabs; everything else is static.
 */
export function ItineraryView({ itinerary }: { itinerary: Itinerary }) {
  const { summary, brief, days, return_leg: returnLeg } = itinerary;
  const [activeDay, setActiveDay] = useState<number | "all">("all");
  const shown = activeDay === "all" ? days : days.filter((d) => d.day_number === activeDay);
  const heroPhoto = firstPhoto(brief.district.media) ?? firstDayPhoto(days);

  return (
    <article>
      {/* --- hero ---------------------------------------------------------- */}
      <header className="relative bg-navy">
        <PhotoFrame
          photo={heroPhoto}
          alt={`${brief.district.name}`}
          tone="district"
          variant="cover"
          rounded="rounded-none"
          sizes="100vw"
          priority
          showCredit={false}
        />
        {/* Scrims, in order. A single vertical gradient left the eyebrow sitting on
            whatever the photograph happened to be doing at the top — unreadable on
            a bright sky. A flat scrim heavy enough to fix that erased the photo
            entirely. So: a light flat floor, a HORIZONTAL ramp that darkens the
            text column while letting the right side of the image read, and a short
            bottom fade into the cream below. Text stays legible on any photograph
            and the photograph is still visible. */}
        <div
          aria-hidden
          className="absolute inset-0 bg-gradient-to-r from-navy from-25% via-navy/75 to-transparent"
        />
        {/* On a phone the text spans the full width, so the horizontal ramp runs
            out before the text does and the eyebrow ends up on open sky. A flat
            scrim would erase the photograph on a wide screen, so it is scoped to
            small viewports where there is no "clear side" to protect. */}
        <div aria-hidden className="absolute inset-0 bg-navy/45 sm:hidden" />
        <div
          aria-hidden
          className="absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-navy to-transparent"
        />
        <div className="relative mx-auto max-w-6xl px-5 pt-16 pb-12 sm:px-8">
          <p className="eyebrow text-marigold">
            {brief.days} {brief.days === 1 ? "day" : "days"} ·{" "}
            {brief.interests.map((i) => i.label).join(" + ")}
          </p>
          <h1 className="mt-3 max-w-3xl font-display text-3xl leading-tight font-semibold text-cream sm:text-4xl">
            {summary.title}
          </h1>
          <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-muted-dim">
            <span>
              {brief.party_size} {brief.party_size === 1 ? "traveller" : "travellers"}
            </span>
            <Dot />
            <span>from {brief.origin.label}</span>
            <Dot />
            <span>{brief.district.name}</span>
          </p>
          {summary.narrative && (
            <p className="mt-5 max-w-2xl text-[15px] leading-relaxed text-cream/85">
              {summary.narrative}
            </p>
          )}

          <dl className="mt-8 grid max-w-2xl grid-cols-2 gap-3 sm:grid-cols-3">
            <Stat label="Total driving" value={formatDistance(summary.total_distance_km)} />
            <Stat label="Time on the road" value={formatDuration(summary.total_travel_minutes)} />
            <Stat
              label="Estimated cost"
              value={formatMoney(summary.estimated_cost)}
              hint="whole party · excl. transport"
            />
          </dl>
          {heroPhoto && (
            <p className="mt-4 text-[10px] text-cream/50">
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
      </header>

      <TripGallery days={days} />

      <div className="mx-auto max-w-6xl px-5 py-10 sm:px-8">
        {summary.warnings.length > 0 && <Warnings warnings={summary.warnings} />}

        {/* --- day tabs ---------------------------------------------------- */}
        <div className="mt-8 flex flex-wrap items-center gap-2">
          <Tab on={activeDay === "all"} onClick={() => setActiveDay("all")}>
            Whole trip
          </Tab>
          {days.map((day) => (
            <Tab
              key={day.day_number}
              on={activeDay === day.day_number}
              onClick={() => setActiveDay(day.day_number)}
            >
              Day {day.day_number}
            </Tab>
          ))}
        </div>

        <ol className="mt-6 space-y-8">
          {shown.map((day) => (
            <li key={day.day_number}>
              <DayBlock day={day} />
            </li>
          ))}
        </ol>

        {returnLeg && activeDay === "all" && (
          <section className="mt-8 rounded-2xl border border-line bg-card p-5">
            <p className="eyebrow text-teal">Heading home</p>
            <p className="mt-2 text-sm">
              Back to {returnLeg.to.label} — {formatDistance(returnLeg.distance_km)},{" "}
              {formatDuration(returnLeg.duration_minutes)}.
            </p>
          </section>
        )}

        <footer className="mt-10 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-5 text-xs text-muted">
          <span>
            Composed by{" "}
            {itinerary.composer === "llm"
              ? `a language model${itinerary.llm_model ? ` (${itinerary.llm_model})` : ""}`
              : "our routing engine"}
          </span>
          <Dot />
          <span>schema v{itinerary.schema_version}</span>
          <Dot />
          <Link href="/plan" className="underline">
            Plan another trip
          </Link>
        </footer>
      </div>
    </article>
  );
}

/**
 * A strip of the stops on this trip, directly under the hero.
 *
 * Only a stop's OWN photograph is eligible here — no falling back to its
 * locality. A strip captioned with place names is read as "this is what these
 * places look like", so a stand-in image of the surrounding taluk would be a
 * quiet lie. Inside a stop card the same fallback is fine, because there the
 * caption says what it is.
 */
function TripGallery({ days }: { days: ItineraryDay[] }) {
  const seen = new Set<string>();
  const shots: { photo: Photo; name: string }[] = [];
  for (const day of days) {
    for (const item of day.items) {
      const photo = firstPhoto(item.media);
      if (photo && !seen.has(photo.title)) {
        seen.add(photo.title);
        shots.push({ photo, name: item.name });
      }
    }
  }
  // Two is enough to read as a strip. A single lonely tile is not, and a
  // single-interest trip in a quiet month legitimately produces one or none.
  if (shots.length < 2) return null;

  return (
    <section className="border-y border-navy-line bg-navy-deep">
      <div className="mx-auto max-w-6xl px-5 py-5 sm:px-8">
        <p className="eyebrow text-muted-dim">On this trip</p>
        {/* Scrolls sideways on a phone rather than wrapping into a tall block
            that pushes the itinerary itself off the first screen. */}
        <ul className="mt-3 flex snap-x gap-3 overflow-x-auto pb-1">
          {shots.slice(0, 8).map(({ photo, name }) => (
            <li key={photo.title} className="w-40 shrink-0 snap-start sm:w-48">
              <div className="relative h-24 sm:h-28">
                <PhotoFrame
                  photo={photo}
                  alt={name}
                  variant="cover"
                  rounded="rounded-lg"
                  sizes="192px"
                  showCredit={false}
                />
              </div>
              <p className="mt-1.5 truncate text-[11.5px] text-cream/85">{name}</p>
              <p className="truncate text-[9.5px] text-muted-dim">
                © {photo.artist} · {photo.license}
              </p>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function DayBlock({ day }: { day: ItineraryDay }) {
  return (
    <section>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-xl font-semibold">
          <span className="text-muted">Day {day.day_number}</span>
          <span aria-hidden className="mx-2 text-line">
            /
          </span>
          {stripDayPrefix(day.title, day.day_number)}
        </h2>
        {day.travel && (
          <span className="eyebrow text-muted">
            {formatDistance(day.travel.distance_km)} ·{" "}
            {formatDuration(day.travel.duration_minutes)} driving
          </span>
        )}
      </div>

      {day.narrative && (
        <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-muted">
          {day.narrative}
        </p>
      )}

      {day.items.length === 0 ? (
        <p className="mt-4 rounded-xl border border-dashed border-line bg-card px-4 py-5 text-sm text-muted">
          No stops today — the day is taken up by the drive.
        </p>
      ) : (
        // The dotted rail is the mockup's timeline. Drawn with a border on a
        // pseudo-element via an absolutely positioned div so it stops cleanly at
        // the last stop instead of running past it.
        <ol className="relative mt-5 space-y-4">
          <span
            aria-hidden
            className="absolute top-3 bottom-3 left-[4.55rem] hidden border-l-2 border-dotted border-terracotta/40 sm:block"
          />
          {day.items.map((item) => (
            <li key={`${item.slot}-${item.poi_id}`}>
              <Stop item={item} />
            </li>
          ))}
        </ol>
      )}

      <StayRow day={day} />
    </section>
  );
}

function Stop({ item }: { item: ItineraryItem }) {
  const permit = item.detail?.requires_permit === true;
  // Its own photograph if it has one, else its taluk's. Activities and stays
  // never get their own — see the fetcher's rules — so the locality stands in.
  const photo = firstPhoto(item.media) ?? firstPhoto(item.region?.media);
  const extra = item.media ? Math.max(0, item.media.length - 1) : 0;

  return (
    <div className="flex gap-3 sm:gap-4">
      <span className="w-12 shrink-0 pt-4 text-right font-mono text-[11px] text-muted">
        {item.start_time_estimate ?? "—"}
      </span>
      <span
        aria-hidden
        className="relative z-10 mt-4 hidden h-3.5 w-3.5 shrink-0 rounded-full border-[3px] border-cream bg-terracotta sm:block"
      />
      <div className="min-w-0 flex-1">
        <LegLine leg={item.leg_from_previous} />
        {/* Stacked on a phone: a 128px thumbnail beside the text left roughly
            twenty characters per line, so the summary ran to five lines. Full
            width above the text reads better and shows the photograph larger. */}
        <div className="flex flex-col gap-3 rounded-xl border border-line bg-card p-3.5 sm:flex-row sm:gap-4">
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
            {/* Most photographed places carry three images. Saying so is more
                use than silently showing one — it tells a reader there is more
                to look at on Commons. */}
            {extra > 0 && (
              <span className="absolute right-1.5 bottom-1.5 rounded-full bg-navy/80 px-1.5 py-0.5 font-mono text-[9.5px] text-cream/90">
                +{extra}
              </span>
            )}
          </div>
          <div className="min-w-0 space-y-1">
            <h3 className="font-display text-[15px] font-semibold">
              {item.name}
              {item.kind === "activity" && (
                <span className="eyebrow ml-2 align-middle text-teal">activity</span>
              )}
            </h3>
            <p className="text-[12.5px] leading-snug text-muted">{item.summary}</p>
            <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11.5px] text-muted">
              {item.duration_minutes && <span>{formatDuration(item.duration_minutes)}</span>}
              {item.cost && (
                <>
                  <Dot />
                  <span>{formatMoney(item.cost)} pp</span>
                </>
              )}
              {permit && (
                <span className="rounded-full border border-terracotta px-2 py-0.5 font-mono text-[9.5px] tracking-wide text-terracotta uppercase">
                  permit required
                </span>
              )}
            </p>
            {item.why_chosen && (
              <p className="border-l-2 border-marigold/50 pl-2.5 text-[12.5px] text-muted italic">
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
              <p className="truncate text-[10px] text-muted/70">
                Photo © {photo.artist} · {photo.license}
                {!firstPhoto(item.media) && " · shows the surrounding area"}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function StayRow({ day }: { day: ItineraryDay }) {
  if (!day.stay) {
    return (
      <p className="mt-4 rounded-xl bg-cream px-4 py-3 text-[13px] text-muted ring-1 ring-line">
        No stay selected for tonight — you will need to arrange your own.
      </p>
    );
  }
  // A stay is a private property and never has a Commons photograph of its own,
  // so this is its locality — labelled as such, because a reader must not think
  // it is a picture of the room.
  const area = firstPhoto(day.stay.region?.media);

  return (
    <div className="mt-4 flex gap-3.5 overflow-hidden rounded-xl bg-navy p-3.5">
      {/* Shown at every width. Hiding it on a phone while still printing
          "photo shows Kalasa" left a caption under nothing. */}
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
        <p className="eyebrow text-marigold">Tonight</p>
        <p className="mt-1 text-[13.5px] text-cream">
          <span className="font-medium">{day.stay.name}</span>
          <span className="text-muted-dim"> · {titleCase(day.stay.stay_type)}</span>
          {day.stay.per_night && (
            <span className="text-muted-dim"> · {formatMoney(day.stay.per_night)}/night</span>
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
    return <p className="mb-1.5 font-mono text-[10.5px] text-muted">↓ a few steps away</p>;
  }
  const parts = [formatDistance(leg.distance_km), formatDuration(leg.duration_minutes)].filter(
    Boolean,
  );
  return (
    <p className="mb-1.5 font-mono text-[10.5px] text-muted">
      ↓ {parts.join(" · ")}
      {leg.source === "static_haversine" && " · estimated"}
    </p>
  );
}

/**
 * Warnings sit above the days, not below them.
 *
 * In this phase the data is hand-compiled and the travel times are estimates, so
 * an itinerary that is stretching or unverified has to say so where the reader
 * will actually see it — before they start reading the plan, not after.
 */
function Warnings({ warnings }: { warnings: ItineraryWarning[] }) {
  return (
    <section className="rounded-2xl border border-terracotta/25 bg-terracotta-soft p-5">
      <p className="eyebrow text-terracotta">Before you go</p>
      <ul className="mt-3 space-y-2 text-[13px] text-ink/80">
        {warnings.map((warning, index) => (
          <li key={`${warning.code}-${index}`} className="flex gap-2">
            <span aria-hidden className="text-terracotta">
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
          ? "border border-navy bg-navy text-cream"
          : "border border-line bg-card text-muted hover:border-navy/40"
      }`}
    >
      {children}
    </button>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl bg-navy-mid/80 px-3.5 py-3 ring-1 ring-navy-line">
      <dt className="eyebrow text-muted-dim">{label}</dt>
      <dd className="mt-1 font-display text-lg text-cream">{value}</dd>
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
