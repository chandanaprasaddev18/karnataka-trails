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
 * The three-step wizard: interests, then trip shape, then submit.
 *
 * Interests are fetched from the API rather than hardcoded, so adding one is a
 * seed-file change on the backend and the two can never disagree.
 */
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
  // Held apart from `submitError` because an impossible brief is not a failure
  // to apologise for — it comes with alternatives the user can take in one click.
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

  if (loadError) {
    return (
      <div className="rounded-lg border border-clay/30 bg-clay-soft p-4 text-sm text-clay">
        {loadError}
      </div>
    );
  }

  if (!interests) {
    return <div className="text-sm text-muted">Loading interests…</div>;
  }

  return (
    <div className="space-y-8">
      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">
          What are you after? <span className="text-muted">(pick one or more)</span>
        </legend>
        <div className="flex flex-wrap gap-2">
          {interests.map((interest) => {
            const active = selected.includes(interest.slug);
            return (
              <button
                key={interest.slug}
                type="button"
                onClick={() => toggle(interest.slug)}
                aria-pressed={active}
                title={interest.description ?? undefined}
                className={`rounded-full border px-3.5 py-1.5 text-sm transition ${
                  active
                    ? "border-moss bg-moss text-white"
                    : "border-line bg-card text-ink hover:border-moss/50"
                }`}
              >
                {interest.label}
              </button>
            );
          })}
        </div>
      </fieldset>

      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Days">
          <Stepper value={days} min={1} max={14} onChange={setDays} suffix={days === 1 ? "day" : "days"} />
        </Field>
        <Field label="People">
          <Stepper
            value={party}
            min={1}
            max={30}
            onChange={setParty}
            suffix={party === 1 ? "person" : "people"}
          />
        </Field>
        <Field label={`Budget — ${budgetLabel(budget)}`}>
          <input
            type="range"
            min={1}
            max={5}
            value={budget}
            onChange={(event) => setBudget(Number(event.target.value))}
            className="w-full accent-moss"
            aria-label="Budget band"
          />
        </Field>
        <Field label="Starting from">
          <select
            value={origin}
            onChange={(event) => setOrigin(event.target.value)}
            className="w-full rounded-md border border-line bg-card px-3 py-2 text-sm"
          >
            {ORIGINS.map((city) => (
              <option key={city} value={city}>
                {city}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Travelling in">
          <select
            value={month}
            onChange={(event) => setMonth(Number(event.target.value))}
            className="w-full rounded-md border border-line bg-card px-3 py-2 text-sm"
          >
            {MONTHS.map((name, index) => (
              <option key={name} value={index + 1}>
                {name}
              </option>
            ))}
          </select>
          <p className="mt-1.5 text-xs text-muted">
            Season matters here — most treks and falls are closed or unsafe outside October to
            February.
          </p>
        </Field>
      </div>

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
        <div className="rounded-lg border border-clay/30 bg-clay-soft p-3 text-sm text-clay">
          {submitError}
        </div>
      )}

      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={submit}
          disabled={selected.length === 0 || submitting}
          className="rounded-md bg-moss px-5 py-2.5 text-sm font-medium text-white transition disabled:cursor-not-allowed disabled:opacity-40"
        >
          {submitting ? "Sending…" : "Build my itinerary"}
        </button>
        {selected.length === 0 && (
          <span className="text-sm text-muted">Pick at least one interest to continue.</span>
        )}
      </div>
    </div>
  );
}

/**
 * Shown when a brief is valid but cannot be planned.
 *
 * The server already worked out what WOULD work, so this offers those as one
 * click instead of telling the reader to go and guess. Nothing here overrides the
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
    <div className="space-y-4 rounded-xl border border-clay/30 bg-clay-soft p-5">
      <div className="space-y-1">
        <h2 className="text-sm font-semibold text-clay">Nothing matches that yet</h2>
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
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{title}</p>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

function Chip({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full border border-moss/40 bg-card px-3 py-1 text-sm text-moss transition hover:bg-moss hover:text-white"
    >
      {children}
    </button>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-2">
      {/* block, not inline: an inline label sits beside an inline-flex stepper
          instead of above it, and space-y has no effect on inline siblings. */}
      <span className="block text-sm font-medium">{label}</span>
      {children}
    </label>
  );
}

function Stepper({
  value,
  min,
  max,
  onChange,
  suffix,
}: {
  value: number;
  min: number;
  max: number;
  onChange: (next: number) => void;
  suffix: string;
}) {
  return (
    <span className="inline-flex items-center gap-3">
      <button
        type="button"
        onClick={() => onChange(Math.max(min, value - 1))}
        disabled={value <= min}
        aria-label="Decrease"
        className="h-8 w-8 rounded-md border border-line bg-card text-lg leading-none disabled:opacity-30"
      >
        −
      </button>
      <span className="min-w-[5.5rem] text-sm tabular-nums">
        {value} {suffix}
      </span>
      <button
        type="button"
        onClick={() => onChange(Math.min(max, value + 1))}
        disabled={value >= max}
        aria-label="Increase"
        className="h-8 w-8 rounded-md border border-line bg-card text-lg leading-none disabled:opacity-30"
      >
        +
      </button>
    </span>
  );
}
