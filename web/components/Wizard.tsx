"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  InfeasibleError,
  MONTHS,
  ORIGINS,
  createPlan,
  fetchInterests,
} from "@/lib/api";
import { budgetLabel } from "@/lib/format";
import type { Infeasible, Interest } from "@/lib/types";

/**
 * The planning form: interests on the left, trip shape in a dark sidebar.
 *
 * Interests come from the API rather than being hardcoded, so adding one is a
 * backend seed change and the two can never disagree. The stamp-card treatment is
 * from the mockup — a passport-stamp metaphor, which suits a trip planner and
 * gives the selected state somewhere obvious to live.
 */

/** One glyph per interest slug. Falls back to a neutral mark for a new tag. */
const GLYPH: Record<string, string> = {
  trekking: "△",
  spiritual: "◐",
  adventurous: "⛰",
  nature: "≈",
  wildlife: "✦",
  "coffee-country": "☕",
  heritage: "◈",
  relaxation: "◎",
  photography: "◉",
  offbeat: "✧",
};

export function Wizard() {
  const router = useRouter();
  const [interests, setInterests] = useState<Interest[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selected, setSelected] = useState<string[]>([]);
  const [days, setDays] = useState(3);
  const [party, setParty] = useState(2);
  const [budget, setBudget] = useState(3);
  const [origin, setOrigin] = useState<string>(ORIGINS[0]);
  const [month, setMonth] = useState<number>(new Date().getMonth() + 1);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // Held apart from `submitError` because an impossible brief is not a failure to
  // apologise for — it arrives with alternatives the user can take in one click.
  const [infeasible, setInfeasible] = useState<Infeasible | null>(null);

  useEffect(() => {
    fetchInterests()
      .then(setInterests)
      .catch((error: unknown) => {
        setLoadError(
          error instanceof ApiError
            ? `Could not load interests (${error.status}). Is the API running on :8000?`
            : "Could not reach the API. Start it with `make api`.",
        );
      });
  }, []);

  function toggle(slug: string) {
    setSelected((current) =>
      current.includes(slug) ? current.filter((s) => s !== slug) : [...current, slug],
    );
  }

  async function submit() {
    setSubmitting(true);
    setSubmitError(null);
    setInfeasible(null);
    try {
      const accepted = await createPlan({
        interests: selected,
        days,
        party_size: party,
        budget_band: budget,
        origin,
        travel_month: month,
      });
      // Generation is async; the itinerary page polls from here.
      router.push(`/itinerary/${accepted.request_id}`);
    } catch (error: unknown) {
      if (error instanceof InfeasibleError) {
        setInfeasible(error.detail);
      } else {
        setSubmitError(error instanceof Error ? error.message : "Something went wrong.");
      }
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col lg:flex-row">
      {/* --- interests ---------------------------------------------------- */}
      <section className="flex-1 px-5 py-10 sm:px-8">
        <p className="eyebrow text-terracotta">Step 1 of 2</p>
        <h1 className="mt-3 font-display text-3xl font-semibold">What draws you in?</h1>
        <p className="mt-2 max-w-lg text-[13.5px] text-muted">
          Pick as many as you like — we shape every stop around them. Season matters here:
          most treks and waterfalls close or turn unsafe outside October to February.
        </p>

        {loadError ? (
          <p className="mt-8 rounded-xl border border-terracotta/30 bg-terracotta-soft p-4 text-sm text-terracotta">
            {loadError}
          </p>
        ) : !interests ? (
          <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {Array.from({ length: 8 }, (_, i) => (
              <div key={i} className="h-[104px] animate-pulse rounded-xl bg-line/60" />
            ))}
          </div>
        ) : (
          <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {interests.map((interest) => {
              const on = selected.includes(interest.slug);
              return (
                <button
                  key={interest.slug}
                  type="button"
                  onClick={() => toggle(interest.slug)}
                  aria-pressed={on}
                  title={interest.description ?? undefined}
                  className={`flex flex-col items-center gap-2.5 rounded-xl px-3 py-5 transition ${
                    on
                      ? "border-[1.5px] border-marigold bg-marigold-soft"
                      : "border-[1.5px] border-dashed border-line bg-card hover:border-marigold/50"
                  }`}
                >
                  <span
                    aria-hidden
                    className={`flex h-11 w-11 items-center justify-center rounded-full font-display text-base transition ${
                      on ? "-rotate-6 bg-marigold text-navy" : "bg-cream text-muted"
                    }`}
                  >
                    {GLYPH[interest.slug] ?? "✦"}
                  </span>
                  <span
                    className={`text-center text-[12.5px] ${on ? "font-medium text-ink" : "text-muted"}`}
                  >
                    {interest.label}
                  </span>
                </button>
              );
            })}
          </div>
        )}

        {infeasible && (
          <NoMatches
            detail={infeasible}
            onPickMonth={(m) => {
              setMonth(m);
              setInfeasible(null);
            }}
            onPickInterest={(slug) => {
              setSelected([slug]);
              setInfeasible(null);
            }}
            onRaiseBudget={(band) => {
              setBudget(band);
              setInfeasible(null);
            }}
          />
        )}

        {submitError && (
          <p className="mt-6 rounded-xl border border-terracotta/30 bg-terracotta-soft p-4 text-sm text-terracotta">
            {submitError}
          </p>
        )}
      </section>

      {/* --- trip shape --------------------------------------------------- */}
      <aside className="w-full shrink-0 bg-navy px-6 py-10 sm:px-8 lg:w-[340px]">
        <h2 className="font-display text-base font-semibold text-cream">Your trip, so far</h2>

        <div className="mt-5">
          <Row label="Days">
            <Stepper value={days} min={1} max={14} onChange={setDays} unit="days" />
          </Row>
          <Row label="Travellers">
            <Stepper value={party} min={1} max={30} onChange={setParty} unit="people" />
          </Row>

          <div className="border-t border-navy-line py-4">
            <div className="flex items-baseline justify-between">
              <span className="text-[13px] text-cream/80">Budget</span>
              <span className="font-mono text-[12px] text-marigold">{budgetLabel(budget)}</span>
            </div>
            <input
              type="range"
              min={1}
              max={5}
              value={budget}
              onChange={(event) => setBudget(Number(event.target.value))}
              aria-label="Budget band"
              className="mt-3 w-full accent-marigold"
            />
          </div>

          <Row label="Starting from">
            <select
              value={origin}
              onChange={(event) => setOrigin(event.target.value)}
              className="rounded-md border border-navy-line bg-navy-mid px-2.5 py-1.5 text-[13px] text-cream"
            >
              {ORIGINS.map((city) => (
                <option key={city} value={city}>
                  {city}
                </option>
              ))}
            </select>
          </Row>
          <Row label="Travelling in">
            <select
              value={month}
              onChange={(event) => setMonth(Number(event.target.value))}
              className="rounded-md border border-navy-line bg-navy-mid px-2.5 py-1.5 text-[13px] text-cream"
            >
              {MONTHS.map((name, index) => (
                <option key={name} value={index + 1}>
                  {name}
                </option>
              ))}
            </select>
          </Row>
        </div>

        <button
          type="button"
          onClick={submit}
          disabled={selected.length === 0 || submitting}
          className="mt-6 w-full rounded-lg bg-marigold py-3.5 font-display text-[14px] font-semibold text-navy transition hover:brightness-105 disabled:cursor-not-allowed disabled:bg-navy-line disabled:text-muted-dim"
        >
          {submitting ? "Building…" : "Plan my trip →"}
        </button>
        {selected.length === 0 && (
          <p className="mt-2.5 text-center text-[12px] text-muted-dim">
            Pick at least one interest.
          </p>
        )}
      </aside>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-navy-line py-4">
      <span className="text-[13px] text-cream/80">{label}</span>
      {children}
    </div>
  );
}

function Stepper({
  value,
  min,
  max,
  onChange,
  unit,
}: {
  value: number;
  min: number;
  max: number;
  onChange: (next: number) => void;
  unit: string;
}) {
  return (
    <span className="flex items-center gap-3">
      <button
        type="button"
        onClick={() => onChange(Math.max(min, value - 1))}
        disabled={value <= min}
        aria-label={`Fewer ${unit}`}
        className="flex h-7 w-7 items-center justify-center rounded-full border border-navy-line text-cream transition hover:border-marigold disabled:opacity-30"
      >
        −
      </button>
      <span className="min-w-[3.5rem] text-center font-mono text-[13px] text-cream">
        {value}
      </span>
      <button
        type="button"
        onClick={() => onChange(Math.min(max, value + 1))}
        disabled={value >= max}
        aria-label={`More ${unit}`}
        className="flex h-7 w-7 items-center justify-center rounded-full border border-navy-line text-cream transition hover:border-marigold disabled:opacity-30"
      >
        +
      </button>
    </span>
  );
}

/**
 * Shown when a brief is valid but cannot be planned.
 *
 * The server already worked out what WOULD work, so this offers those in one
 * click rather than telling the reader to go and guess. Nothing here overrides the
 * seasonal filter — putting someone on a monsoon trek is the failure this is
 * deliberately keeping.
 */
function NoMatches({
  detail,
  onPickMonth,
  onPickInterest,
  onRaiseBudget,
}: {
  detail: Infeasible;
  onPickMonth: (month: number) => void;
  onPickInterest: (slug: string) => void;
  onRaiseBudget: (band: number) => void;
}) {
  return (
    <div className="mt-8 space-y-4 rounded-2xl border border-terracotta/30 bg-terracotta-soft p-5">
      <div className="space-y-1">
        <p className="eyebrow text-terracotta">Nothing matches that yet</p>
        <p className="text-sm text-ink/80">{detail.message}</p>
      </div>

      {detail.suggested_months.length > 0 && (
        <Suggestions title="Try a different month">
          {detail.suggested_months.map((month) => (
            <Chip key={month} onClick={() => onPickMonth(month)}>
              {MONTHS[month - 1]}
            </Chip>
          ))}
        </Suggestions>
      )}

      {detail.suggested_interests.length > 0 && (
        <Suggestions title={`Or something good in ${MONTHS[detail.asked_month - 1]}`}>
          {detail.suggested_interests.map((interest) => (
            <Chip key={interest.slug} onClick={() => onPickInterest(interest.slug)}>
              {interest.label}
            </Chip>
          ))}
        </Suggestions>
      )}

      {detail.min_budget_band !== null && (
        <Suggestions title="Or spend a little more">
          <Chip onClick={() => onRaiseBudget(detail.min_budget_band as number)}>
            Raise budget to {budgetLabel(detail.min_budget_band)}
          </Chip>
        </Suggestions>
      )}
    </div>
  );
}

function Suggestions({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="eyebrow text-muted">{title}</p>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

function Chip({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full border border-teal/40 bg-card px-3 py-1 text-sm text-teal transition hover:bg-teal hover:text-cream"
    >
      {children}
    </button>
  );
}
