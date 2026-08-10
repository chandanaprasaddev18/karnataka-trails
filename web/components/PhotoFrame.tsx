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
  // photographs sitting next to them.
  place: "from-teal/70 to-navy",
  activity: "from-terracotta/70 to-navy",
  stay: "from-navy-soft to-navy",
  district: "from-marigold/60 to-navy",
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
 * The locally downloaded copy wins. Hotlinking Wikimedia was tried and their CDN
 * returns 429 under mild bursts, so the remote URL is only a fallback for a row
 * whose download failed.
 */
function photoSrc(photo: Photo): string {
  return photo.local_path ?? photo.thumb_url ?? photo.url;
}

/** First photo of a media array, or null. Media is a list for future galleries. */
export function firstPhoto(media: Photo[] | null | undefined): Photo | null {
  return media && media.length > 0 ? media[0] : null;
}
