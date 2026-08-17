/**
 * Wire types for the itinerary payload.
 *
 * SOURCE OF TRUTH: `api/src/tripplan/domain/models.py`. These are hand-written
 * rather than generated, which is a deliberate tradeoff — codegen would need the
 * API running at build time. The payload carries `schema_version`, so a mismatch
 * is detectable rather than silent: bump it on the server and the renderer can
 * refuse a version it does not understand.
 *
 * Money is integer PAISE everywhere, matching the backend. Never a float.
 */

export const SUPPORTED_SCHEMA_VERSION = 1;

export type JobStatus = "queued" | "running" | "succeeded" | "failed";
export type PoiKind = "place" | "stay" | "activity";
export type ComposerName = "llm" | "deterministic";
export type TravelSource = "static_haversine" | "osrm" | "maps_api";

/**
 * An attributable photograph from Wikimedia Commons.
 *
 * `artist`, `license` and `source_page` are required, not optional: the backend
 * refuses to store an image whose licence it could not read, because crediting
 * the author is a condition of the licence.
 */
export interface Photo {
  url: string;
  thumb_url: string;
  title: string;
  artist: string;
  license: string;
  license_url: string | null;
  source_page: string;
  width: number | null;
  height: number | null;
  /**
   * Path to our own downloaded copy, e.g. "/photos/ab12.jpg". Preferred over
   * `url` so no page view hits Wikimedia — their CDN rate-limits (429) and a
   * page should not depend on a free external service being up.
   */
  local_path: string | null;
  /** Set only on gallery entries: the place the photograph shows. */
  caption?: string;
}

export interface Money {
  min_paise: number;
  max_paise: number;
  per_person: boolean;
}

export interface GeoPoint {
  lat: number;
  lon: number;
}

export interface TravelLeg {
  distance_km: number;
  duration_minutes: number;
  source: TravelSource;
}

export interface GuideRef {
  guide_id: string;
  name: string;
  languages: string[];
  contact: Record<string, unknown>;
  is_verified: boolean;
}

export interface RegionRef {
  slug: string;
  name: string;
  media: Photo[];
}

export interface ItineraryItem {
  slot: number;
  kind: PoiKind;
  poi_id: string;
  name: string;
  summary: string;
  /** The taluk. Its imagery is the fallback for stops with no photo of their own. */
  region: RegionRef | null;
  why_chosen: string | null;
  start_time_estimate: string | null;
  duration_minutes: number | null;
  cost: Money | null;
  point: GeoPoint;
  media: Photo[];
  detail: Record<string, unknown>;
  leg_from_previous: TravelLeg | null;
  guides: GuideRef[];
}

export interface StayCard {
  poi_id: string;
  /** Used to request this stay — the booking API takes slugs, never UUIDs. */
  slug: string;
  name: string;
  stay_type: string;
  /** Stays have no photograph of their own; the card falls back to this. */
  region: RegionRef | null;
  per_night: Money | null;
  point: GeoPoint;
  contact: Record<string, unknown>;
  media: Photo[];
  meals_included: boolean;
  amenities: string[];
}

export interface ItineraryDay {
  day_number: number;
  title: string;
  narrative: string | null;
  travel: TravelLeg | null;
  stay: StayCard | null;
  items: ItineraryItem[];
  /**
   * The driven shape of the day as [lat, lon] pairs, from the routing provider.
   * Empty when it could not be fetched — the map is then not drawn at all, rather
   * than drawn as straight lines between stops, which in the Ghats would depict a
   * road that does not exist.
   */
  route: [number, number][];
}

/** Codes the UI styles differently. Kept in sync with WarningCode in models.py. */
export type WarningCode =
  | "long_travel_day"
  | "no_stay_available"
  | "thin_day"
  | "unverified_data"
  | "fallback_composer"
  | "permit_required"
  | "interest_unmet"
  | "arrival_day"
  | "late_finish"
  | "packed_day";

export interface ItineraryWarning {
  code: WarningCode;
  day_number: number | null;
  message: string;
}

export interface Itinerary {
  schema_version: number;
  itinerary_id: string | null;
  request_id: string | null;
  generated_at: string;
  composer: ComposerName;
  llm_provider: string | null;
  llm_model: string | null;
  candidate_set_hash: string | null;
  brief: {
    mode: PlanMode;
    anchor: { kind: "poi" | "region"; slug: string; label: string; point: GeoPoint } | null;
    radius_km: number | null;
    interests: { slug: string; label: string }[];
    days: number;
    party_size: number;
    budget_band: number;
    origin: { label: string; lat: number; lon: number };
    district: { slug: string; name: string; media: Photo[] };
  };
  summary: {
    title: string;
    narrative: string | null;
    total_distance_km: number;
    total_travel_minutes: number;
    estimated_cost: Money | null;
    warnings: ItineraryWarning[];
  };
  days: ItineraryDay[];
  return_leg: {
    from_poi_id: string | null;
    to: { label: string; lat: number; lon: number };
    distance_km: number;
    duration_minutes: number;
    source: TravelSource;
  } | null;
}

/**
 * The structured body of a 422 from POST /api/plan.
 *
 * A brief can be perfectly valid and still impossible — trekking in monsoon.
 * The server says which constraint blocked it and what would work instead, so
 * the UI can offer a fix rather than a dead end.
 */
export interface Infeasible {
  message: string;
  reason:
    | "out_of_season"
    | "budget_too_low"
    | "nothing_tagged"
    | "nothing_in_radius"
    | "no_data";
  asked_month: number;
  suggested_months: number[];
  suggested_interests: { slug: string; label: string }[];
  min_budget_band: number | null;
  /** Location mode: a wider radius that would find something. */
  suggested_radius_km: number | null;
}

/** A district card on the home page. */
export interface District {
  slug: string;
  name: string;
  published_places: number;
  media: Photo[];
  top_interests: string[];
  /** One photo per place in the district, for the home mosaic. */
  gallery: Photo[];
  /** Months (1-12) in which this district has anything open. */
  open_months: number[];
}

/** A place or locality a location-mode trip can be planned around. */
export interface Anchor {
  kind: "poi" | "region";
  slug: string;
  label: string;
  sublabel: string;
  lat: number;
  lon: number;
  /** Published places and activities within 60 km — shown so nobody picks an
      anchor with nothing around it. */
  nearby: number;
}

export type PlanMode = "interest" | "location" | "district";

export interface Interest {
  slug: string;
  label: string;
  description: string | null;
  /** A photograph of a published place carrying this tag. Null is normal. */
  photo: Photo | null;
  /** Which place that photograph shows — rendered, never implied. */
  photo_caption: string | null;
}

export interface PlanAccepted {
  request_id: string;
  job_id: string;
  status: JobStatus;
  poll_url: string;
  session_token: string;
}

export interface PlanStatus {
  request_id: string;
  job: {
    status: JobStatus;
    stage: string | null;
    attempts: number;
    max_attempts: number;
    error_code: string | null;
    error_detail: string | null;
  };
  itinerary_id: string | null;
  itinerary: Itinerary | null;
}

/* --- Phase 4: booking requests ------------------------------------------- */

export type BookingKind = "stay" | "guide" | "activity";
export type BookingStatus =
  | "requested"
  | "sent"
  | "confirmed"
  | "declined"
  | "withdrawn";

/** What a request points at, snapshotted server-side when it was made. */
export interface BookingTarget {
  kind: BookingKind;
  name: string;
  locality: string | null;
  /** Empty for everything we currently hold — no stay has a verified contact. */
  contact: Record<string, unknown>;
  price_note: string | null;
  is_verified: boolean;
  is_placeholder: boolean;
}

export interface Booking {
  id: string;
  kind: BookingKind;
  status: BookingStatus;
  party_size: number;
  check_in: string | null;
  check_out: string | null;
  note: string | null;
  itinerary_id: string | null;
  day_number: number | null;
  target: BookingTarget;
  /** The real channel a request was handed to. Null means we could not send it. */
  sent_via: string | null;
  created_at: string;
  /**
   * Whether we hold any channel that could carry this request. False for every
   * row this release can produce — the UI says so rather than showing a status
   * that implies someone is dealing with it.
   */
  deliverable: boolean;
}

/* --- Phase 5: marketplace ------------------------------------------------- */

export interface Product {
  slug: string;
  name: string;
  summary: string | null;
  price_note: string | null;
  unit: string | null;
  vendor_name: string;
  vendor_slug: string;
  vendor_contact: Record<string, unknown>;
  region_name: string | null;
  categories: string[];
}

/** What a place is known for producing. No seller, no price — that is the point. */
export interface Speciality {
  region_slug: string;
  region_name: string;
  region_kind: string;
  category_slug: string;
  category_label: string;
  note: string;
  best_months: number[];
  source: string;
  data_confidence: number;
  products: Product[];
}

/** How much of the marketplace is real. Rendered, not hidden. */
export interface MarketStats {
  specialities: number;
  categories: number;
  published_vendors: number;
  published_products: number;
  withheld_vendors: number;
}

export interface MarketView {
  specialities: Speciality[];
  stats: MarketStats;
}
