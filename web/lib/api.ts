import type {
  Anchor,
  Booking,
  BookingKind,
  District,
  Infeasible,
  Interest,
  PlanAccepted,
  MarketView,
  PlanMode,
  PlanStatus,
  Product,
} from "./types";

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

/** A fresh anonymous identity for this browser. Not a credential — it scopes rows. */
function mintSessionToken(): string {
  const token =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
  storeSessionToken(token);
  return token;
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
  mode: PlanMode;
  interests: string[];
  days: number;
  party_size: number;
  budget_band: number;
  origin: string;
  travel_month: number | null;
  /** Location mode: the slug of an anchor from `fetchAnchors`. */
  anchor?: string | null;
  radius_km?: number | null;
}

/**
 * Anchors matching a typed fragment, for location mode.
 *
 * Searched on the server rather than filtered in the browser: `nearby` is a
 * per-row radius count only the database can do cheaply, and the list becomes
 * every published POI in Karnataka as more districts are seeded.
 */
export async function fetchAnchors(query: string): Promise<Anchor[]> {
  const response = await fetch(
    `${BASE}/api/anchors?q=${encodeURIComponent(query)}`,
    { cache: "no-store" },
  );
  if (!response.ok) throw new ApiError(await parseError(response), response.status);
  return response.json();
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


/* --- Phase 4: booking requests ------------------------------------------- */

export interface BookingInput {
  kind: BookingKind;
  /** A slug we publish. Prices, contacts and names are resolved server-side. */
  slug: string;
  party_size: number;
  check_in?: string | null;
  check_out?: string | null;
  note?: string | null;
  itinerary_id?: string | null;
  day_number?: number | null;
}

/** Raised when this browser already has an open request for the same thing. */
export class DuplicateBookingError extends Error {
  constructor(readonly bookingId: string | null) {
    super("You already have an open request for this.");
  }
}

export async function createBooking(input: BookingInput): Promise<Booking> {
  // Mint our own token when we have none rather than relying on the server's,
  // which arrives in a response header — and a cross-origin header is invisible
  // to JavaScript unless the server exposes it. It does now, but a request that
  // silently lands under an unknowable identity is a bad enough failure to be
  // worth defending against twice.
  const token = readSessionToken() ?? mintSessionToken();
  const response = await fetch(`${BASE}/api/bookings`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-Session-Token": token } : {}),
    },
    body: JSON.stringify(input),
  });
  if (response.status === 409) {
    const body = await response.json().catch(() => null);
    throw new DuplicateBookingError(body?.detail?.booking_id ?? null);
  }
  if (!response.ok) throw new ApiError(await parseError(response), response.status);
  // A first-time visitor gets their token back in a header; store it or their
  // request would be invisible to them on the next page load.
  const issued = response.headers.get("X-Session-Token");
  if (issued) storeSessionToken(issued);
  return response.json();
}

export async function fetchBookings(): Promise<Booking[]> {
  const token = readSessionToken();
  if (!token) return [];
  const response = await fetch(`${BASE}/api/bookings`, {
    headers: { "X-Session-Token": token },
    cache: "no-store",
  });
  if (!response.ok) throw new ApiError(await parseError(response), response.status);
  return response.json();
}

export async function withdrawBooking(bookingId: string): Promise<Booking> {
  const token = readSessionToken();
  if (!token) throw new ApiError("no session", 401);
  const response = await fetch(`${BASE}/api/bookings/${bookingId}/withdraw`, {
    method: "POST",
    headers: { "X-Session-Token": token },
  });
  if (!response.ok) throw new ApiError(await parseError(response), response.status);
  return response.json();
}

/* --- Phase 5: marketplace ------------------------------------------------- */

export async function fetchSpecialities(params: {
  district?: string;
  itineraryId?: string;
}): Promise<MarketView> {
  const query = params.itineraryId
    ? `itinerary_id=${encodeURIComponent(params.itineraryId)}`
    : `district=${encodeURIComponent(params.district ?? "chikkamagaluru")}`;
  const response = await fetch(`${BASE}/api/market/specialities?${query}`, {
    cache: "no-store",
  });
  if (!response.ok) throw new ApiError(await parseError(response), response.status);
  return response.json();
}

export async function fetchProducts(category?: string): Promise<Product[]> {
  const query = category ? `?category=${encodeURIComponent(category)}` : "";
  const response = await fetch(`${BASE}/api/market/products${query}`, { cache: "no-store" });
  if (!response.ok) throw new ApiError(await parseError(response), response.status);
  return response.json();
}
