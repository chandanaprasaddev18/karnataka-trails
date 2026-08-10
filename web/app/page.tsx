import Link from "next/link";
import { PhotoFrame, firstPhoto } from "@/components/PhotoFrame";
import { fetchDistricts } from "@/lib/api";
import type { District, Photo } from "@/lib/types";

/**
 * Home. Server-rendered, because the district cards are content rather than
 * interaction and there is no reason to make the browser fetch them.
 *
 * `no-store` on the fetch keeps the card counts honest as seed data is published.
 */
export default async function HomePage() {
  let districts: District[] = [];
  let unreachable = false;
  try {
    districts = await fetchDistricts();
  } catch {
    unreachable = true;
  }

  // The gallery is split rather than shared: the hero takes the first three
  // photographs and the mosaic takes the rest, so no place appears twice on the
  // page. A short gallery therefore shrinks the mosaic rather than repeating.
  const gallery = districts.flatMap((d) => d.gallery);

  return (
    <main>
      <Hero photos={gallery.slice(0, 3)} />
      <Districts districts={districts} unreachable={unreachable} />
      <Mosaic photos={gallery.slice(3, 8)} />
    </main>
  );
}

function Hero({ photos }: { photos: Photo[] }) {
  return (
    <section
      className="bg-navy"
      style={{
        // Two soft glows, marigold and teal, so the navy is not flat. Inline
        // because Tailwind has no ergonomic syntax for layered radial gradients.
        backgroundImage:
          "radial-gradient(ellipse 60% 50% at 12% 0%, rgba(232,153,47,0.20), transparent)," +
          "radial-gradient(ellipse 50% 45% at 88% 8%, rgba(22,121,127,0.22), transparent)",
      }}
    >
      <div className="mx-auto max-w-6xl px-5 pt-16 pb-20 sm:px-8">
        <div className="grid items-center gap-10 lg:grid-cols-[1.05fr_1fr]">
          <div>
            <p className="eyebrow text-marigold">Chikkamagaluru · coffee country</p>
            <h1 className="mt-4 font-display text-4xl leading-[1.08] font-semibold text-cream sm:text-5xl">
              Tell us what you are chasing.
              <br />
              We will build the <span className="text-marigold">whole trip</span>.
            </h1>
            <p className="mt-5 max-w-xl text-[15px] leading-relaxed text-muted-dim">
              A temple in the mist, a ridge before sunrise, or four unhurried days on an
              estate verandah. Every stop comes from our own curated database, routed from
              your doorstep, with somewhere to sleep each night.
            </p>
          </div>
          <HeroCollage photos={photos} />
        </div>

        <div className="mt-12 grid gap-4 sm:grid-cols-3">
          <ModeCard
            href="/plan"
            glyph="◐"
            accent="marigold"
            title="By interest"
            body="Spiritual, trekking, coffee trails, waterfalls — pick what pulls you."
          />
          {/* Phase 2. Shown so the shape of the product is legible, but plainly
              not offered — a card that looks live and then 404s is worse than one
              that admits it is not ready. */}
          <ModeCard
            glyph="◇"
            accent="teal"
            title="By location"
            body="Already know where? Type or pick any place in Karnataka."
            comingSoon
          />
          <ModeCard
            glyph="▵"
            accent="terracotta"
            title="By district"
            body="Go deep on one district, end to end, at your own pace."
            comingSoon
          />
        </div>
      </div>
    </section>
  );
}

/**
 * Three real photographs beside the headline.
 *
 * The hero used to be type on a gradient, which made a page about places show
 * none of them. This is the cheapest honest fix: the first thing a visitor sees
 * is three stops that are actually in the database, each credited.
 *
 * It renders nothing rather than a placeholder when photographs are missing —
 * the layout collapses back to a single column and reads as intentional.
 */
function HeroCollage({ photos }: { photos: Photo[] }) {
  if (photos.length < 3) return null;

  return (
    <div aria-hidden className="hidden grid-cols-2 grid-rows-2 gap-3 lg:grid lg:h-[21rem]">
      <div className="relative row-span-2">
        <PhotoFrame
          photo={photos[0]}
          alt=""
          variant="cover"
          rounded="rounded-2xl"
          sizes="(max-width: 1024px) 0px, 25vw"
          priority
        />
      </div>
      {photos.slice(1, 3).map((photo) => (
        <div key={photo.title} className="relative">
          <PhotoFrame
            photo={photo}
            alt=""
            variant="cover"
            rounded="rounded-2xl"
            sizes="(max-width: 1024px) 0px, 18vw"
          />
        </div>
      ))}
    </div>
  );
}

const ACCENT: Record<string, { chip: string; text: string }> = {
  marigold: { chip: "bg-marigold text-navy", text: "text-marigold" },
  teal: { chip: "bg-teal text-cream", text: "text-teal" },
  terracotta: { chip: "bg-terracotta text-cream", text: "text-terracotta" },
};

function ModeCard({
  href,
  glyph,
  accent,
  title,
  body,
  comingSoon = false,
}: {
  href?: string;
  glyph: string;
  accent: keyof typeof ACCENT;
  title: string;
  body: string;
  comingSoon?: boolean;
}) {
  const tone = ACCENT[accent];
  const inner = (
    <>
      <span
        aria-hidden
        className={`flex h-11 w-11 items-center justify-center rounded-full font-display text-lg ${
          comingSoon ? "bg-navy-line text-muted-dim" : tone.chip
        }`}
      >
        {glyph}
      </span>
      <h2 className="mt-4 font-display text-[17px] font-semibold text-cream">{title}</h2>
      <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted-dim">{body}</p>
      <span
        className={`eyebrow mt-4 block ${comingSoon ? "text-muted" : tone.text}`}
      >
        {comingSoon ? "Coming in phase 2" : "Start here →"}
      </span>
    </>
  );

  const base =
    "rounded-2xl border p-6 text-left transition " +
    (comingSoon
      ? "border-navy-line/60 bg-navy-mid/40 cursor-default"
      : "border-navy-line bg-navy-mid hover:border-marigold/60 hover:bg-navy-soft");

  return href ? (
    <Link href={href} className={`block ${base}`}>
      {inner}
    </Link>
  ) : (
    <div className={base} aria-disabled>
      {inner}
    </div>
  );
}

function Districts({
  districts,
  unreachable,
}: {
  districts: District[];
  unreachable: boolean;
}) {
  return (
    <section className="mx-auto max-w-6xl px-5 py-14 sm:px-8">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="font-display text-xl font-semibold">Where we can plan today</h2>
        <span className="eyebrow text-muted">
          {districts.length > 0
            ? `${districts.length} district${districts.length === 1 ? "" : "s"} live`
            : ""}
        </span>
      </div>

      {unreachable ? (
        <p className="mt-6 rounded-xl border border-terracotta/30 bg-terracotta-soft p-4 text-sm text-terracotta">
          Could not reach the API. Start it with <code>make api</code>.
        </p>
      ) : districts.length === 0 ? (
        <p className="mt-6 rounded-xl border border-line bg-card p-4 text-sm text-muted">
          No districts have published places yet. Seed them with{" "}
          <code>make seed &amp;&amp; make publish</code>.
        </p>
      ) : (
        /* One district is the Phase 1 reality, and a lone third-width card next
           to two empty columns looks like a page that failed to load. It gets a
           wide layout until there are enough to fill a row. */
        <div
          className={`mt-6 grid gap-5 ${
            districts.length === 1 ? "" : "sm:grid-cols-2 lg:grid-cols-3"
          }`}
        >
          {districts.map((district, index) => (
            <DistrictCard
              key={district.slug}
              district={district}
              priority={index === 0}
              wide={districts.length === 1}
            />
          ))}
        </div>
      )}
    </section>
  );
}

/**
 * A mosaic of the district's places.
 *
 * Every tile is a real photograph of a real stop, captioned with the place it
 * shows — so it doubles as a preview of what is actually in the database rather
 * than decoration. Uneven spans keep it from reading as a grid of stamps.
 */
function Mosaic({ photos }: { photos: Photo[] }) {
  const tiles = photos.slice(0, 5);
  if (tiles.length < 5) return null;

  // Five tiles pack a 6x2 grid exactly, with no half-empty trailing row: one
  // large tile carries the block and the other four fill around it at two sizes.
  const spans = [
    "col-span-3 row-span-2",
    "col-span-2",
    "col-span-1",
    "col-span-2",
    "col-span-1",
  ];

  return (
    <section className="bg-navy-deep">
      <div className="mx-auto max-w-6xl px-5 py-14 sm:px-8">
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="font-display text-xl font-semibold text-cream">
            What you would actually see
          </h2>
          <span className="eyebrow text-muted-dim">Wikimedia Commons · CC</span>
        </div>
        <div className="mt-6 grid auto-rows-[105px] grid-cols-6 gap-2.5 sm:auto-rows-[145px]">
          {tiles.map((photo, index) => (
            <figure
              key={photo.title}
              className={`relative overflow-hidden rounded-xl ${spans[index] ?? "col-span-1"}`}
            >
              <PhotoFrame
                photo={photo}
                alt={photo.caption ?? "A place in the district"}
                variant="cover"
                rounded="rounded-none"
                sizes="(max-width: 640px) 50vw, 33vw"
                showCredit={false}
              />
              <div
                aria-hidden
                className="absolute inset-0 bg-gradient-to-t from-navy-deep/90 via-transparent to-transparent"
              />
              <figcaption className="absolute bottom-2 left-3 right-3">
                <span className="block truncate font-display text-[13px] font-semibold text-cream">
                  {photo.caption}
                </span>
                <span className="block truncate text-[9.5px] text-cream/55">
                  © {photo.artist} · {photo.license}
                </span>
              </figcaption>
            </figure>
          ))}
        </div>
      </div>
    </section>
  );
}

function DistrictCard({
  district,
  priority,
  wide = false,
}: {
  district: District;
  priority: boolean;
  wide?: boolean;
}) {
  const photo = firstPhoto(district.media);

  if (wide) {
    return (
      <Link
        href="/plan"
        className="group grid overflow-hidden rounded-2xl border border-line bg-card transition hover:shadow-lg sm:grid-cols-[1.3fr_1fr]"
      >
        <div className="relative h-56 sm:h-full sm:min-h-[15rem]">
          <PhotoFrame
            photo={photo}
            alt={`${district.name} district`}
            tone="district"
            variant="cover"
            rounded="rounded-none"
            sizes="(max-width: 640px) 100vw, 55vw"
            priority={priority}
            /* The credit is a link, and this whole card is a link. Nested
               anchors are invalid HTML and break hydration, so the attribution
               is rendered as text in the pane below instead. */
            showCredit={false}
          />
        </div>
        <div className="flex flex-col justify-center gap-3 p-6 sm:p-8">
          <p className="eyebrow text-teal">Live now</p>
          <h3 className="font-display text-2xl font-semibold">{district.name}</h3>
          <p className="text-[13.5px] leading-relaxed text-muted">
            {district.published_places} places and activities, each with a source and a
            confidence rating, plus stays for every night of the trip.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {district.top_interests.map((label) => (
              <span
                key={label}
                className="rounded-full bg-teal-soft px-2.5 py-0.5 text-[11px] text-teal"
              >
                {label}
              </span>
            ))}
          </div>
          <span className="eyebrow text-marigold transition group-hover:text-terracotta">
            Plan a trip here →
          </span>
          {photo && (
            <p className="truncate text-[10px] text-muted">
              Photo © {photo.artist} · {photo.license}
            </p>
          )}
        </div>
      </Link>
    );
  }

  return (
    <Link
      href="/plan"
      className="group block overflow-hidden rounded-2xl border border-line bg-card transition hover:-translate-y-0.5 hover:shadow-lg"
    >
      <div className="relative h-44">
        <PhotoFrame
          photo={photo}
          alt={`${district.name} district`}
          tone="district"
          variant="cover"
          rounded="rounded-none"
          sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
          priority={priority}
          showCredit={false}
        />
        <div
          aria-hidden
          className="absolute inset-0 bg-gradient-to-t from-navy/85 via-navy/25 to-transparent"
        />
        <div className="absolute bottom-3 left-4 right-4">
          <h3 className="font-display text-lg font-semibold text-cream">{district.name}</h3>
          <p className="eyebrow mt-0.5 text-cream/70">
            {district.published_places} places &amp; activities
          </p>
        </div>
      </div>
      <div className="space-y-2 p-4">
        <div className="flex flex-wrap gap-1.5">
          {district.top_interests.map((label) => (
            <span
              key={label}
              className="rounded-full bg-teal-soft px-2.5 py-0.5 text-[11px] text-teal"
            >
              {label}
            </span>
          ))}
        </div>
        {photo && (
          <p className="truncate text-[10px] text-muted">
            Photo © {photo.artist} · {photo.license}
          </p>
        )}
      </div>
    </Link>
  );
}
