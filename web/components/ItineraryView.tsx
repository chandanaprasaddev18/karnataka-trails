import Link from "next/link";
import { formatDistance, formatDuration, formatMoney, titleCase } from "@/lib/format";
import type { Itinerary, ItineraryDay, ItineraryItem, ItineraryWarning } from "@/lib/types";

/**
 * The itinerary, as cards.
 *
 * Everything rendered here is a field on the payload — no client-side arithmetic,
 * no re-derived totals. If a number is on screen, the server computed it.
 */
export function ItineraryView({ itinerary }: { itinerary: Itinerary }) {
  const { summary, brief, days, return_leg: returnLeg } = itinerary;

  return (
    <article className="space-y-8">
      <header className="space-y-4">
        <h1 className="text-3xl font-semibold tracking-tight">{summary.title}</h1>
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
          <span>
            {brief.days} {brief.days === 1 ? "day" : "days"}
          </span>
          <Dot />
          <span>
            {brief.party_size} {brief.party_size === 1 ? "person" : "people"}
          </span>
          <Dot />
          <span>from {brief.origin.label}</span>
          <Dot />
          <span>{brief.district.name}</span>
        </p>
        <div className="flex flex-wrap gap-1.5">
          {brief.interests.map((interest) => (
            <span
              key={interest.slug}
              className="rounded-full bg-moss-soft px-2.5 py-1 text-xs text-moss"
            >
              {interest.label}
            </span>
          ))}
        </div>
        {summary.narrative && <p className="max-w-2xl leading-relaxed">{summary.narrative}</p>}
        <dl className="grid grid-cols-2 gap-4 rounded-xl border border-line bg-card p-4 sm:grid-cols-3">
          <Stat label="Total driving" value={formatDistance(summary.total_distance_km)} />
          <Stat label="Time on the road" value={formatDuration(summary.total_travel_minutes)} />
          <Stat
            label="Estimated cost"
            value={formatMoney(summary.estimated_cost)}
            hint="whole party · excludes transport and meals"
          />
        </dl>
      </header>

      {summary.warnings.length > 0 && <Warnings warnings={summary.warnings} />}

      <ol className="space-y-6">
        {days.map((day) => (
          <li key={day.day_number}>
            <DayCard day={day} />
          </li>
        ))}
      </ol>

      {returnLeg && (
        <section className="rounded-xl border border-line bg-card p-5">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
            Heading home
          </h2>
          <p className="mt-2 text-sm">
            Back to {returnLeg.to.label} — {formatDistance(returnLeg.distance_km)},{" "}
            {formatDuration(returnLeg.duration_minutes)}.
          </p>
        </section>
      )}

      <footer className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-4 text-xs text-muted">
        <span>
          Composed by{" "}
          {itinerary.composer === "llm"
            ? `a language model${itinerary.llm_model ? ` (${itinerary.llm_model})` : ""}`
            : "our routing engine"}
        </span>
        <Dot />
        <span>schema v{itinerary.schema_version}</span>
        <Dot />
        <Link href="/" className="underline">
          Plan another trip
        </Link>
      </footer>
    </article>
  );
}

function DayCard({ day }: { day: ItineraryDay }) {
  return (
    <section className="overflow-hidden rounded-xl border border-line bg-card">
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-5 py-4">
        <h2 className="font-semibold">
          <span className="text-muted">Day {day.day_number}</span>
          <span className="mx-2 text-line">/</span>
          {stripDayPrefix(day.title, day.day_number)}
        </h2>
        {day.travel && (
          <span className="text-xs text-muted">
            {formatDistance(day.travel.distance_km)} · {formatDuration(day.travel.duration_minutes)}{" "}
            driving
          </span>
        )}
      </div>

      {day.narrative && (
        <p className="border-b border-line px-5 py-3 text-sm leading-relaxed text-muted">
          {day.narrative}
        </p>
      )}

      {day.items.length === 0 ? (
        <p className="px-5 py-4 text-sm text-muted">
          No stops today — the day is taken up by the drive.
        </p>
      ) : (
        <ol className="divide-y divide-line">
          {day.items.map((item) => (
            <li key={`${item.slot}-${item.poi_id}`}>
              <ItemRow item={item} />
            </li>
          ))}
        </ol>
      )}

      <div className="border-t border-line bg-paper/60 px-5 py-4">
        {day.stay ? (
          <div className="space-y-1">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted">Tonight</p>
            <p className="text-sm">
              <span className="font-medium">{day.stay.name}</span>
              <span className="text-muted"> · {titleCase(day.stay.stay_type)}</span>
              {day.stay.per_night && (
                <span className="text-muted"> · {formatMoney(day.stay.per_night)} per night</span>
              )}
              {day.stay.meals_included && <span className="text-muted"> · meals included</span>}
            </p>
            {day.stay.amenities.length > 0 && (
              <p className="text-xs text-muted">
                {day.stay.amenities.map((a) => titleCase(a)).join(" · ")}
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted">
            No stay selected for tonight — you will need to arrange your own.
          </p>
        )}
      </div>
    </section>
  );
}

function ItemRow({ item }: { item: ItineraryItem }) {
  const permit = item.detail?.requires_permit === true;
  return (
    <div className="px-5 py-4">
      <LegLine leg={item.leg_from_previous} />
      <div className="flex items-baseline gap-3">
        <span className="w-12 shrink-0 text-sm tabular-nums text-muted">
          {item.start_time_estimate ?? "—"}
        </span>
        <div className="min-w-0 space-y-1">
          <h3 className="font-medium">
            {item.name}
            {item.kind === "activity" && (
              <span className="ml-2 rounded bg-moss-soft px-1.5 py-0.5 text-[11px] text-moss">
                activity
              </span>
            )}
          </h3>
          <p className="text-sm text-muted">{item.summary}</p>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
            {item.duration_minutes && <span>{formatDuration(item.duration_minutes)}</span>}
            {item.cost && (
              <>
                <Dot />
                <span>{formatMoney(item.cost)} per person</span>
              </>
            )}
            {permit && (
              <>
                <Dot />
                <span className="font-medium text-clay">permit required</span>
              </>
            )}
          </p>
          {item.why_chosen && (
            <p className="border-l-2 border-moss/30 pl-3 text-sm italic text-muted">
              {item.why_chosen}
            </p>
          )}
          {item.guides.length > 0 && (
            <p className="text-xs text-muted">
              Guide:{" "}
              {item.guides
                .map((g) => `${g.name}${g.languages.length ? ` (${g.languages.join("/")})` : ""}`)
                .join(", ")}
            </p>
          )}
        </div>
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
    return <p className="mb-2 text-xs text-muted">↓ a few steps away</p>;
  }
  const parts = [formatDistance(leg.distance_km), formatDuration(leg.duration_minutes)].filter(
    Boolean,
  );
  return (
    <p className="mb-2 text-xs text-muted">
      ↓ {parts.join(", ")}
      {leg.source === "static_haversine" && " (estimated)"}
    </p>
  );
}

/**
 * Warnings are shown prominently rather than tucked away. In this phase the data
 * is hand-compiled and travel times are estimates, so an itinerary that is
 * stretching or unverified has to say so where the reader will see it.
 */
function Warnings({ warnings }: { warnings: ItineraryWarning[] }) {
  return (
    <section className="rounded-xl border border-clay/25 bg-clay-soft p-5">
      <h2 className="text-sm font-semibold text-clay">Before you go</h2>
      <ul className="mt-3 space-y-2 text-sm text-ink/80">
        {warnings.map((warning, index) => (
          <li key={`${warning.code}-${index}`} className="flex gap-2">
            <span aria-hidden className="text-clay">
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

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-0.5 text-sm font-medium">{value}</dd>
      {hint && <dd className="text-[11px] text-muted">{hint}</dd>}
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

/** The composer prefixes its titles with "Day N — "; the card already shows that. */
function stripDayPrefix(title: string, dayNumber: number): string {
  return title.replace(new RegExp(`^Day\\s*${dayNumber}\\s*[—–-]\\s*`), "");
}
