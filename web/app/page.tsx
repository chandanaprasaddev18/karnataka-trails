import Link from "next/link";
import { PhotoFrame, firstPhoto } from "@/components/PhotoFrame";
import { fetchDistricts } from "@/lib/api";
import type { District } from "@/lib/types";

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

  return (
    <main>
      <Hero />
      <Districts districts={districts} unreachable={unreachable} />
    </main>
  );
}

function Hero() {
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
        <p className="eyebrow text-marigold">Chikkamagaluru · coffee country</p>
        <h1 className="mt-4 max-w-3xl font-display text-4xl leading-[1.08] font-semibold text-cream sm:text-5xl">
          Tell us what you are chasing.
          <br />
          We will build the <span className="text-marigold">whole trip</span>.
        </h1>
        <p className="mt-5 max-w-xl text-[15px] leading-relaxed text-muted-dim">
          A temple in the mist, a ridge before sunrise, or four unhurried days on an estate
          verandah. Every stop comes from our own curated database, routed from your doorstep,
          with somewhere to sleep each night.
        </p>

        <div className="mt-10 grid gap-4 sm:grid-cols-3">
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
        <div className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {districts.map((district, index) => (
            <DistrictCard key={district.slug} district={district} priority={index === 0} />
          ))}
        </div>
      )}
    </section>
  );
}

function DistrictCard({ district, priority }: { district: District; priority: boolean }) {
  const photo = firstPhoto(district.media);
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
