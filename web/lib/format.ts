import type { Money } from "./types";

/**
 * Display helpers.
 *
 * Money arrives as integer paise and is only ever divided for DISPLAY. No
 * arithmetic happens in the browser: totals are computed server-side, so the
 * number a user reads cannot disagree with the number that was stored.
 */

const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

export function formatMoney(money: Money | null | undefined): string {
  if (!money) return "—";
  const low = inr.format(money.min_paise / 100);
  if (money.min_paise === money.max_paise) return low;
  return `${low}–${inr.format(money.max_paise / 100)}`;
}

export function formatDuration(minutes: number | null | undefined): string {
  if (!minutes) return "";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest} min`;
  if (rest === 0) return `${hours} hr`;
  return `${hours} hr ${rest} min`;
}

export function formatDistance(km: number): string {
  return `${Math.round(km).toLocaleString("en-IN")} km`;
}

const BUDGET_LABELS = ["", "Shoestring", "Budget", "Mid-range", "Premium", "Luxury"];

export function budgetLabel(band: number): string {
  return BUDGET_LABELS[band] ?? `Band ${band}`;
}

export function titleCase(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
