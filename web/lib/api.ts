import type { District, Infeasible, Interest, PlanAccepted, PlanStatus } from "./types";

/**
 * API client.
 *
 * The session token is kept in localStorage and sent back on every plan request.
 * Phase 1 has no accounts; this is what will let "my trips" work without a
 * backfill, and it means two browsers do not share an identity.
 */
const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const SESSION_KEY = "tripplan.session_token";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** A valid brief that cannot be planned, with the alternatives that would work. */
export class InfeasibleError extends Error {
  constructor(readonly detail: Infeasible) {
    super(detail.message);
  }
}

function isInfeasible(detail: unknown): detail is Infeasible {
  return (
    typeof detail === "object" &&
    detail !== null &&
    "reason" in detail &&
    "suggested_months" in detail
  );
}

function readSessionToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(SESSION_KEY);
}

function storeSessionToken(token: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(SESSION_KEY, token);
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    // FastAPI validation errors arrive as a list of per-field objects; a raw
    // JSON dump in the UI is useless, so flatten to something readable.
    if (Array.isArray(body?.detail)) {
      return body.detail
        .map((d: { loc?: string[]; msg?: string }) =>
          `${d.loc?.slice(1).join(".") ?? "request"}: ${d.msg ?? "invalid"}`,
        )
        .join("; ");
    }
    if (typeof body?.detail === "string") return body.detail;
    return response.statusText;
  } catch {
    return response.statusText;
  }
}

export async function fetchInterests(): Promise<Interest[]> {
  const response = await fetch(`${BASE}/api/taxonomy/interests`, { cache: "no-store" });
  if (!response.ok) throw new ApiError(await parseError(response), response.status);
  return response.json();
}

export async function fetchDistricts(): Promise<District[]> {
  const response = await fetch(`${BASE}/api/districts`, { cache: "no-store" });
  if (!response.ok) throw new ApiError(await parseError(response), response.status);
  return response.json();
}

export interface PlanInput {
  interests: string[];
  days: number;
  party_size: number;
  budget_band: number;
  origin: string;
  travel_month: number | null;
}

export async function createPlan(input: PlanInput): Promise<PlanAccepted> {
  const token = readSessionToken();
  const response = await fetch(`${BASE}/api/plan`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-Session-Token": token } : {}),
    },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    // A 422 carrying a structured body is an impossible brief, not a malformed
    // one; the caller can act on it rather than just showing the text.
    if (response.status === 422) {
      const body = await response.json().catch(() => null);
      if (body && isInfeasible(body.detail)) throw new InfeasibleError(body.detail);
      throw new ApiError(await parseError(response), response.status);
    }
    throw new ApiError(await parseError(response), response.status);
  }
  const accepted: PlanAccepted = await response.json();
  storeSessionToken(accepted.session_token);
  return accepted;
}

export async function fetchPlanStatus(requestId: string): Promise<PlanStatus> {
  const response = await fetch(`${BASE}/api/plan/${requestId}`, { cache: "no-store" });
  if (!response.ok) throw new ApiError(await parseError(response), response.status);
  return response.json();
}

/** Origins the backend can resolve. Mirrors domain/origins.py until Phase 3 geocoding. */
export const ORIGINS = [
  "Bengaluru",
  "Mysuru",
  "Mangaluru",
  "Hassan",
  "Shivamogga",
  "Hubballi",
  "Udupi",
  "Chikkamagaluru",
] as const;

export const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;
