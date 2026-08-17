"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  InfeasibleError,
  MONTHS,
  ORIGINS,
  createPlan,
  fetchAnchors,
  fetchDistricts,
  fetchInterests,
} from "@/lib/api";
import { PhotoFrame } from "@/components/PhotoFrame";
import { budgetLabel } from "@/lib/format";
import type { Anchor, District, Infeasible, Interest, PlanMode } from "@/lib/types";

/**
 * One wizard, three modes.
 *
 * The three planning modes differ in exactly one thing — what you choose in step
 * one — and that mirrors the backend, where they differ in exactly one WHERE
 * clause. Building three wizards would have let them drift apart; this way the
 * trip shape (days, travellers, budget, origin, month), the submit path, and the
 * infeasible-brief handling are literally the same code.
 *
 * What each mode requires is enforced on the server (`engine/brief.py`). Here it
 * only decides whether the button is enabled, so the two can disagree in one
 * direction only: the UI may be stricter, never looser.
 */

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

const RADII = [25, 50, 75, 100, 150, 200];

const COPY: Record<PlanMode, { eyebrow: string; title: string; blurb: string }> = {
  interest: {
    eyebrow: "Plan by interest",
    title: "What draws you in?",
    blurb:
      "Pick as many as you like — every stop is chosen against them. Season matters here: most treks and waterfalls close or turn unsafe outside October to February.",
  },
  location: {
    eyebrow: "Plan by location",
    title: "Where are you starting from?",
    blurb:
      "Choose a place or a town and how far you are willing to roam. We look outside the district boundary too — a good stop 30 km away does not care which district it is in.",
  },
  district: {
    eyebrow: "Plan by district",
    title: "Which district, end to end?",
    blurb:
      "The whole district's best, in the order the roads allow. Interests are optional here — add them and they nudge the ranking rather than filtering anything out.",
  },
};

export function PlanWizard({
  mode,
  initialInterest,
  initialAnchor,
  initialDistrict,
}: {
  mode: PlanMode;
  initialInterest?: string;
  /** Slug handed over by the global search, preselected once it resolves. */
  initialAnchor?: string;
  /** Slug handed over by a district card on the home page. */
  initialDistrict?: string;
}) {
  const router = useRouter();

  // step 1, per mode
  const [interests, setInterests] = useState<Interest[] | null>(null);
  const [selected, setSelected] = useState<string[]>(initialInterest ? [initialInterest] : []);
  const [districts, setDistricts] = useState<District[] | null>(null);
  const [district, setDistrict] = useState(initialDistrict ?? "chikkamagaluru");
  const [anchors, setAnchors] = useState<Anchor[]>([]);
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const [query, setQuery] = useState(initialAnchor ?? "");
  const [radius, setRadius] = useState(50);

  // shared trip shape
  const [days, setDays] = useState(3);
  const [party, setParty] = useState(2);
  const [budget, setBudget] = useState(3);
  const [origin, setOrigin] = useState<string>(ORIGINS[0]);
  const [month, setMonth] = useState<number>(new Date().getMonth() + 1);

  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // Held apart from submitError: an impossible brief is not a failure to
  // apologise for, it arrives with alternatives the user can take in one click.
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
    if (mode === "district") {
      fetchDistricts().then(setDistricts).catch(() => setDistricts([]));
    }
  }, [mode]);

  // Anchor search, debounced. An empty query is a real query here — the server
  // answers it with the anchors that have the most around them, which is a far
  // better starting point than a blank list.
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (mode !== "location") return;
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      fetchAnchors(query).then(setAnchors).catch(() => setAnchors([]));
    }, 220);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [query, mode]);

  // The anchor in play: what the user picked, or the one the global search handed
  // over once the server's results confirm we hold it. Derived rather than pushed
  // into state by an effect — an effect here caused a second render pass on every
  // search result, and needed care to avoid clobbering a later manual choice.
  //
  // Matched against the server's list rather than trusted from the URL, so a slug
  // we do not hold selects nothing instead of submitting something the engine will
  // reject.
  const effectiveAnchor =
    anchor ?? (initialAnchor ? anchors.find((a) => a.slug === initialAnchor) ?? null : null);

  const toggle = (slug: string) =>
    setSelected((current) =>
      current.includes(slug) ? current.filter((s) => s !== slug) : [...current, slug],
    );

  const ready =
    mode === "interest" ? selected.length > 0 : mode === "location" ? !!effectiveAnchor : true;

  const submit = useCallback(async () => {
    setSubmitting(true);
    setSubmitError(null);
    setInfeasible(null);
    try {
      const accepted = await createPlan({
        mode,
        interests: selected,
        days,
        party_size: party,
        budget_band: budget,
        origin,
        travel_month: month,
        district,
        anchor: mode === "location" ? effectiveAnchor?.slug : null,
        radius_km: mode === "location" ? radius : null,
      });
      router.push(`/itinerary/${accepted.request_id}`);
    } catch (error: unknown) {
      if (error instanceof InfeasibleError) setInfeasible(error.detail);
      else setSubmitError(error instanceof Error ? error.message : "Something went wrong.");
      setSubmitting(false);
    }
    // EVERY value read above belongs here. `district` was missing, so this
    // callback captured the district the page loaded with and kept sending it: the
    // user picked Mysuru, the UI showed Mysuru selected, and the request said
    // Chikkamagaluru. A stale closure is invisible in the types and in the DOM —
    // the only visible symptom was the wrong itinerary at the end.
  }, [
    mode,
    selected,
    days,
    party,
    budget,
    origin,
    month,
    district,
    effectiveAnchor,
    radius,
    router,
  ]);

  const copy = COPY[mode];

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8">
      <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
        <section>
          <p className="eyebrow text-gold">{copy.eyebrow}</p>
          <h1 className="mt-2 font-display text-[30px] leading-tight font-bold">{copy.title}</h1>
          <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-muted">{copy.blurb}</p>

          {loadError && (
            <p className="mt-6 rounded-xl border border-rust/40 bg-rust-soft p-4 text-[13px] text-rust">
              {loadError}
            </p>
          )}

          {mode === "interest" && (
            <InterestGrid interests={interests} selected={selected} onToggle={toggle} />
          )}

          {mode === "district" && (
            <DistrictChoice
              districts={districts}
              chosen={district}
              onChoose={setDistrict}
              interests={interests}
              selected={selected}
              onToggle={toggle}
            />
          )}

          {mode === "location" && (
            <AnchorChoice
              anchors={anchors}
              anchor={effectiveAnchor}
              query={query}
              radius={radius}
              onQuery={setQuery}
              onPick={setAnchor}
              onRadius={setRadius}
            />
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
              onWiden={(km) => {
                setRadius(km);
                setInfeasible(null);
              }}
              onShorten={(value) => {
                setDays(value);
                setInfeasible(null);
              }}
            />
          )}

          {submitError && (
            <p className="mt-6 rounded-xl border border-rust/40 bg-rust-soft p-4 text-[13px] text-rust">
              {submitError}
            </p>
          )}
        </section>

        {/* --- trip shape ---------------------------------------------------- */}
        <aside className="panel h-fit p-5 lg:sticky lg:top-5">
          <h2 className="font-display text-[15px] font-semibold">Your trip, so far</h2>

          <div className="mt-3">
            <Row label="Days">
              <Stepper value={days} min={1} max={14} onChange={setDays} unit="days" />
            </Row>
            <Row label="Travellers">
              <Stepper value={party} min={1} max={30} onChange={setParty} unit="people" />
            </Row>

            <div className="border-t border-line py-3.5">
              <div className="flex items-baseline justify-between">
                <span className="text-[13px] text-muted">Budget</span>
                <span className="font-mono text-[12px] text-gold">{budgetLabel(budget)}</span>
              </div>
              <input
                type="range"
                min={1}
                max={5}
                value={budget}
                onChange={(event) => setBudget(Number(event.target.value))}
                aria-label="Budget band"
                className="mt-3 w-full accent-gold"
              />
            </div>

            <Row label="Starting from">
              <Select value={origin} onChange={setOrigin}>
                {ORIGINS.map((city) => (
                  <option key={city} value={city}>
                    {city}
                  </option>
                ))}
              </Select>
            </Row>
            <Row label="Travelling in">
              <Select value={String(month)} onChange={(v) => setMonth(Number(v))}>
                {MONTHS.map((name, index) => (
                  <option key={name} value={index + 1}>
                    {name}
                  </option>
                ))}
              </Select>
            </Row>
            {mode === "location" && effectiveAnchor && (
              <Row label="Around">
                <span className="max-w-[9.5rem] truncate text-right font-mono text-[12px] text-gold">
                  {effectiveAnchor.label} · {radius} km
                </span>
              </Row>
            )}
          </div>

          <button
            type="button"
            onClick={submit}
            disabled={!ready || submitting}
            className="mt-5 w-full rounded-xl bg-gold py-3 font-display text-[14px] font-semibold text-ink-950 transition hover:brightness-110 disabled:cursor-not-allowed disabled:bg-ink-700 disabled:text-muted-dim"
          >
            {submitting ? "Building…" : "Plan my trip →"}
          </button>
          {!ready && (
            <p className="mt-2 text-center text-[11.5px] text-muted-dim">
              {mode === "interest" ? "Pick at least one interest." : "Choose a place to plan around."}
            </p>
          )}
          <p className="mt-3 text-center text-[11px] leading-snug text-muted-dim">
            Generation runs as a background job; the next screen follows it.
          </p>
        </aside>
      </div>
    </div>
  );
}

/* --- step one, per mode --------------------------------------------------- */

function InterestGrid({
  interests,
  selected,
  onToggle,
}: {
  interests: Interest[] | null;
  selected: string[];
  onToggle: (slug: string) => void;
}) {
  if (!interests) {
    return (
      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {Array.from({ length: 8 }, (_, i) => (
          <div key={i} className="h-[168px] animate-pulse rounded-xl bg-ink-800" />
        ))}
      </div>
    );
  }

  return (
    <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {interests.map((interest) => {
        const on = selected.includes(interest.slug);
        return (
          <button
            key={interest.slug}
            type="button"
            onClick={() => onToggle(interest.slug)}
            aria-pressed={on}
            title={
              interest.photo_caption
                ? `${interest.description ?? interest.label} — photo shows ${interest.photo_caption}`
                : (interest.description ?? undefined)
            }
            className={`group overflow-hidden rounded-xl border text-left transition ${
              on ? "border-gold ring-2 ring-gold/30" : "border-line hover:border-gold/50"
            }`}
          >
            {/* A photograph of a place that actually carries this tag, captioned
                with the place. The glyph is the fallback for a tag with no
                photographed place — it must not borrow another interest's image. */}
            <span className="relative block h-24 w-full">
              <PhotoFrame
                photo={interest.photo}
                alt=""
                variant="cover"
                rounded="rounded-none"
                sizes="(max-width: 640px) 45vw, 220px"
                showCredit={false}
              />
              <span
                aria-hidden
                className="absolute inset-0 bg-gradient-to-t from-ink-950 via-ink-950/25 to-transparent"
              />
              {!interest.photo && (
                <span
                  aria-hidden
                  className="absolute inset-0 flex items-center justify-center font-display text-2xl text-cream/70"
                >
                  {GLYPH[interest.slug] ?? "✦"}
                </span>
              )}
              {on && (
                <span
                  aria-hidden
                  className="absolute top-2 right-2 flex h-6 w-6 items-center justify-center rounded-full bg-gold text-[13px] font-bold text-ink-950"
                >
                  ✓
                </span>
              )}
              <span className="absolute inset-x-2.5 bottom-1.5">
                <span className="block truncate font-display text-[13.5px] font-semibold">
                  {interest.label}
                </span>
                {interest.photo_caption && (
                  <span className="block truncate text-[9px] text-muted-dim">
                    {interest.photo_caption} · © {interest.photo?.artist}
                  </span>
                )}
              </span>
            </span>
            <span
              className={`block px-2.5 py-2 text-[11px] leading-snug ${
                on ? "bg-gold-soft text-cream" : "bg-ink-850 text-muted-dim"
              }`}
            >
              {interest.description ?? " "}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function DistrictChoice({
  districts,
  chosen,
  onChoose,
  interests,
  selected,
  onToggle,
}: {
  districts: District[] | null;
  chosen: string;
  onChoose: (slug: string) => void;
  interests: Interest[] | null;
  selected: string[];
  onToggle: (slug: string) => void;
}) {
  return (
    <div className="mt-6 space-y-6">
      <div className="grid gap-3 sm:grid-cols-2">
        {(districts ?? []).map((district) => {
          const on = district.slug === chosen;
          return (
            <button
              key={district.slug}
              type="button"
              onClick={() => onChoose(district.slug)}
              aria-pressed={on}
              className={`flex items-center gap-3 rounded-xl border p-3 text-left transition ${
                on ? "border-gold bg-gold-soft" : "border-line bg-ink-850 hover:border-gold/50"
              }`}
            >
              <span className="relative h-14 w-20 shrink-0 overflow-hidden rounded-lg">
                <PhotoFrame
                  photo={district.media[0] ?? null}
                  alt=""
                  tone="district"
                  variant="cover"
                  rounded="rounded-none"
                  sizes="80px"
                  showCredit={false}
                />
              </span>
              <span className="min-w-0">
                <span className="block font-display text-[15px] font-semibold">
                  {district.name}
                </span>
                <span className="block text-[11.5px] text-muted">
                  {district.published_places} places · {district.top_interests.join(", ")}
                </span>
              </span>
            </button>
          );
        })}
      </div>

      <div>
        <p className="eyebrow text-muted-dim">Optional — nudge the ranking</p>
        <p className="mt-1 text-[12px] text-muted-dim">
          In this mode interests do not filter anything out; they only decide what comes
          first.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {(interests ?? []).map((interest) => {
            const on = selected.includes(interest.slug);
            return (
              <button
                key={interest.slug}
                type="button"
                onClick={() => onToggle(interest.slug)}
                aria-pressed={on}
                className={`rounded-full border px-3 py-1.5 text-[12px] transition ${
                  on
                    ? "border-gold bg-gold-soft text-gold"
                    : "border-line text-muted hover:border-gold/50"
                }`}
              >
                {interest.label}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function AnchorChoice({
  anchors,
  anchor,
  query,
  radius,
  onQuery,
  onPick,
  onRadius,
}: {
  anchors: Anchor[];
  anchor: Anchor | null;
  query: string;
  radius: number;
  onQuery: (value: string) => void;
  onPick: (value: Anchor) => void;
  onRadius: (value: number) => void;
}) {
  return (
    <div className="mt-6 space-y-5">
      <div>
        <input
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          placeholder="Search a place or a town — Mudigere, Mullayanagiri, Kalasa…"
          aria-label="Search for a place to plan around"
          className="w-full rounded-xl border border-line bg-ink-850 px-4 py-3 text-[14px] text-cream placeholder:text-muted-dim"
        />
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {anchors.map((option) => {
            const on = anchor?.slug === option.slug && anchor?.kind === option.kind;
            return (
              <li key={`${option.kind}-${option.slug}`}>
                <button
                  type="button"
                  onClick={() => onPick(option)}
                  aria-pressed={on}
                  className={`flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition ${
                    on
                      ? "border-gold bg-gold-soft"
                      : "border-line bg-ink-850 hover:border-gold/50"
                  }`}
                >
                  <span
                    aria-hidden
                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[12px] ${
                      option.kind === "region"
                        ? "bg-ink-700 text-cream"
                        : "bg-teal-soft text-teal"
                    }`}
                  >
                    {option.kind === "region" ? "▣" : "◉"}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] font-medium">
                      {option.label}
                    </span>
                    <span className="block truncate text-[11px] text-muted-dim">
                      {option.sublabel}
                    </span>
                  </span>
                  {/* How much is actually near it. Shown because an anchor with
                      nothing around it produces a thin trip, and the user should
                      be able to see that before submitting rather than after. */}
                  <span className="shrink-0 font-mono text-[10.5px] text-muted-dim">
                    {option.nearby} near
                  </span>
                </button>
              </li>
            );
          })}
          {anchors.length === 0 && (
            <li className="text-[12.5px] text-muted-dim">
              Nothing matches that name in our data yet.
            </li>
          )}
        </ul>
      </div>

      <div className="panel p-4">
        <div className="flex items-baseline justify-between">
          <span className="text-[13px] text-muted">How far will you roam?</span>
          <span className="font-mono text-[12px] text-gold">{radius} km</span>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {RADII.map((km) => (
            <button
              key={km}
              type="button"
              onClick={() => onRadius(km)}
              aria-pressed={radius === km}
              className={`rounded-full border px-3 py-1.5 font-mono text-[11.5px] transition ${
                radius === km
                  ? "border-gold bg-gold-soft text-gold"
                  : "border-line text-muted hover:border-gold/50"
              }`}
            >
              {km} km
            </button>
          ))}
        </div>
        <p className="mt-3 text-[11.5px] leading-relaxed text-muted-dim">
          Measured as the crow flies from your anchor, so the drive is longer. District
          boundaries are ignored — a good stop 30 km away does not care which district it
          sits in.
        </p>
      </div>
    </div>
  );
}

/* --- shared bits ---------------------------------------------------------- */

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-line py-3.5">
      <span className="text-[13px] text-muted">{label}</span>
      {children}
    </div>
  );
}

function Select({
  value,
  onChange,
  children,
}: {
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="rounded-lg border border-line bg-ink-800 px-2.5 py-1.5 text-[13px] text-cream"
    >
      {children}
    </select>
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
    <span className="flex items-center gap-2.5">
      <button
        type="button"
        onClick={() => onChange(Math.max(min, value - 1))}
        disabled={value <= min}
        aria-label={`Fewer ${unit}`}
        className="flex h-7 w-7 items-center justify-center rounded-full border border-line text-cream transition hover:border-gold disabled:opacity-30"
      >
        −
      </button>
      <span className="min-w-[2.5rem] text-center font-mono text-[13px]">{value}</span>
      <button
        type="button"
        onClick={() => onChange(Math.min(max, value + 1))}
        disabled={value >= max}
        aria-label={`More ${unit}`}
        className="flex h-7 w-7 items-center justify-center rounded-full border border-line text-cream transition hover:border-gold disabled:opacity-30"
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
 * click rather than telling the reader to go and guess. Nothing here overrides
 * the seasonal filter — putting someone on a monsoon trek is the failure this is
 * deliberately keeping.
 */
function NoMatches({
  detail,
  onPickMonth,
  onPickInterest,
  onRaiseBudget,
  onWiden,
  onShorten,
}: {
  detail: Infeasible;
  onPickMonth: (month: number) => void;
  onPickInterest: (slug: string) => void;
  onRaiseBudget: (band: number) => void;
  onWiden: (km: number) => void;
  onShorten: (days: number) => void;
}) {
  return (
    <div className="mt-7 space-y-4 rounded-2xl border border-rust/35 bg-rust-soft p-5">
      <div className="space-y-1">
        <p className="eyebrow text-rust">Nothing matches that yet</p>
        <p className="text-[13.5px] text-cream/90">{detail.message}</p>
      </div>

      {detail.max_days && (
        <Suggestions title="Shorten the trip">
          <Chip onClick={() => onShorten(detail.max_days ?? 1)}>
            {detail.max_days} {detail.max_days === 1 ? "day" : "days"}
          </Chip>
        </Suggestions>
      )}

      {detail.suggested_radius_km && (
        <Suggestions title="Widen the search">
          <Chip onClick={() => onWiden(detail.suggested_radius_km ?? 50)}>
            {detail.suggested_radius_km} km
          </Chip>
        </Suggestions>
      )}

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

      {detail.min_budget_band && (
        <Suggestions title="Or raise the budget">
          <Chip onClick={() => onRaiseBudget(detail.min_budget_band ?? 5)}>
            {budgetLabel(detail.min_budget_band)}
          </Chip>
        </Suggestions>
      )}
    </div>
  );
}

function Suggestions({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="eyebrow text-muted-dim">{title}</p>
      <div className="mt-2 flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

function Chip({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full border border-line bg-ink-850 px-3 py-1.5 text-[12.5px] text-cream transition hover:border-gold hover:text-gold"
    >
      {children}
    </button>
  );
}
