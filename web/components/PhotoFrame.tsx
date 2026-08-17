import Image from "next/image";
import type { Photo } from "@/lib/types";

/**
 * Renders a photograph with its credit, or a generated fallback.
 *
 * Two rules, both load-bearing:
 *
 * 1. **Attribution is rendered, not stored and forgotten.** These are other
 *    people's photographs under Creative Commons terms; crediting the author and
 *    naming the licence is a condition of use, not decoration. A photo with no
 *    readable author or licence never reaches this component — the fetcher drops
 *    it.
 *
 * 2. **A missing photo becomes a generated gradient, never a stand-in image.**
 *    Roughly half the records have no Commons photograph — every stay, every
 *    activity, and the places nobody has photographed. A generic "misty hill"
 *    under a name it does not depict would be a small lie, so the fallback is
 *    obviously not a photograph: a colour wash keyed to the kind of thing it is.
 *
 * `variant` exists because next/image's `fill` needs a positioned parent, and the
 * two uses want different positioning. Passing extra classes to override it does
 * NOT work: Tailwind emits `relative` after `absolute` in its own canonical
 * order, so a caller's `absolute inset-0` loses to this component's `relative`
 * regardless of attribute order, the wrapper collapses to zero height, and the
 * image silently renders nothing.
 *
 *   "sized" — the wrapper is `relative` and the caller gives it width/height.
 *   "cover" — the wrapper is `absolute inset-0`, filling a parent the caller has
 *             already positioned.
 */

type Tone = "place" | "stay" | "activity" | "district";

const FALLBACK: Record<Tone, string> = {
  // Distinct enough to read as intentional, muted enough not to fight the real
  // photographs sitting next to them. On a dark ground these are deliberately
  // dim: a bright block would draw more attention than the photographs do.
  place: "from-teal/35 to-ink-900",
  activity: "from-rust/30 to-ink-900",
  stay: "from-ink-700 to-ink-900",
  district: "from-gold/25 to-ink-900",
};

export function PhotoFrame({
  photo,
  alt,
  tone = "place",
  variant = "sized",
  className = "",
  sizes = "100vw",
  priority = false,
  rounded = "rounded-xl",
  showCredit = true,
  grade = true,
}: {
  photo: Photo | null | undefined;
  alt: string;
  tone?: Tone;
  variant?: "sized" | "cover";
  className?: string;
  sizes?: string;
  priority?: boolean;
  rounded?: string;
  showCredit?: boolean;
  /** Apply the unified photo grade. Off only where a raw image is wanted. */
  grade?: boolean;
}) {
  const position = variant === "cover" ? "absolute inset-0" : "relative";

  if (!photo) {
    return (
      <div
        aria-hidden
        className={`bg-gradient-to-br ${FALLBACK[tone]} ${position} ${rounded} ${className}`}
      />
    );
  }

  return (
    <div
      className={`group overflow-hidden ${grade ? "photo-grade" : ""} ${position} ${rounded} ${className}`}
    >
      <Image
        src={photoSrc(photo)}
        alt={alt}
        fill
        sizes={sizes}
        priority={priority}
        // Remote images go straight from Wikimedia's CDN to the browser. Routing
        // them through our optimizer would put every viewer's images on one server
        // IP, which is exactly what got 429ed before.
        unoptimized={REMOTE_PHOTOS}
        className="object-cover"
      />
      {showCredit && <PhotoCredit photo={photo} />}
    </div>
  );
}

/**
 * The credit line. Small and low-contrast so it does not compete with the
 * content, but always present and always a real link to the source page.
 */
export function PhotoCredit({ photo }: { photo: Photo }) {
  return (
    <a
      href={photo.source_page}
      target="_blank"
      rel="noopener noreferrer license"
      className="absolute right-0 bottom-0 max-w-full truncate bg-navy/70 px-2 py-0.5 text-[10px] text-cream/80 transition hover:bg-navy hover:text-cream"
      title={`${photo.artist} — ${photo.license}. Opens Wikimedia Commons.`}
    >
      © {photo.artist} · {photo.license}
    </a>
  );
}

/**
 * Where to load the bytes from.
 *
 * Locally, the downloaded copy wins: `web/public/photos/` holds 73 MB of images
 * that are gitignored, so nothing about a page view depends on Wikimedia being up.
 *
 * A DEPLOYED build cannot use them — 73 MB has no business in git — so
 * `NEXT_PUBLIC_PHOTO_SOURCE=remote` switches to the Commons URL that every photo
 * row already carries. That is not a regression of the earlier 429 problem: those
 * came from our own server fetching dozens of images through Next's optimizer from
 * one IP. `unoptimized` (below) means each viewer's browser fetches Wikimedia's
 * CDN directly, which is what a CDN is for.
 *
 * The honest limitation: if Wikimedia throttles a viewer, that viewer sees the
 * gradient fallback rather than the photograph. Putting the files on object storage
 * is the fix when this stops being a prototype.
 */
const REMOTE_PHOTOS = process.env.NEXT_PUBLIC_PHOTO_SOURCE === "remote";

function photoSrc(photo: Photo): string {
  if (REMOTE_PHOTOS) return photo.thumb_url ?? photo.url;
  return photo.local_path ?? photo.thumb_url ?? photo.url;
}

/** First photo of a media array, or null. Media is a list for future galleries. */
export function firstPhoto(media: Photo[] | null | undefined): Photo | null {
  return media && media.length > 0 ? media[0] : null;
}
