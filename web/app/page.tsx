import Link from "next/link";
import { PhotoFrame, firstPhoto } from "@/components/PhotoFrame";
import { fetchDistricts, fetchInterests } from "@/lib/api";
import type { District, Interest, Photo } from "@/lib/types";

/**
 * Home, in the dark dashboard theme.
 *
 * Server-rendered: the district and interest cards are content, not interaction,
 * and there is no reason to make the browser fetch them.
 *
 * The reference design puts a star rating and a "from ₹" price on every getaway
 * card. We have neither — no reviews, no inventory — so those slots carry things
 * we do hold: how many places are published, which months the district is open,
 * and how far it is from the default origin. A fabricated 4.8 would be the single
 * most damaging thing on this page.
 */
export default async function HomePage() {
  let districts: District[] = [];
  let interests: Interest[] = [];
  let unreachable = false;
  try {
    [districts, interests] = await Promise.all([fetchDistricts(), fetchInterests()]);
  } catch {
    unreachable = true;
  }

  const gallery = districts.flatMap((d) => d.gallery);

  return (
    <main className="mx-auto max-w-[1400px] px-4 py-5 sm:px-6 lg:px-8">
      {unreachable && (
        <p className="mb-5 rounded-xl border border-rust/40 bg-rust-soft px-4 py-3 text-[13px] text-rust">
          Could not reach the API. Start it with <code>make api</code>.
        </p>
      )}

      <Hero photo={firstPhoto(districts[0]?.media) ?? gallery[0]} />
      <Modes />
      <Getaways districts={districts} />
      <Inspiration interests={interests} />
      <Gallery photos={gallery.slice(0, 5)} />
      <Truths />
    </main>
  );
}

function Hero({ photo }: { photo: Photo | undefined | null }) {
  return (
    <section className="relative overflow-hidden rounded-2xl border border-line">
      <div className="relative h-[300px] sm:h-[360px]">
        <PhotoFrame
          photo={photo}
          alt=""
          tone="district"
          variant="cover"
          rounded="rounded-none"
          sizes="100vw"
          priority
          showCredit={false}
        />
        {/* Horizontal ramp, not a flat wash: the text column needs a dark ground
            and the photograph needs to survive on the right. */}
        <div
          aria-hidden
          className="absolute inset-0 bg-gradient-to-r from-ink-950 via-ink-950/85 to-ink-950/20"
        />
        <div
          aria-hidden
          className="absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-ink-950 to-transparent"
        />

        <div className="relative flex h-full flex-col justify-center gap-4 p-6 sm:p-10">
          <p className="eyebrow text-gold">Chikkamagaluru · coffee country</p>
          <h1 className="max-w-2xl font-display text-[34px] leading-[1.05] font-bold sm:text-[46px]">
            Tell us what you are chasing.
            <br />
            We plan the <span className="text-gold">whole trip</span>.
          </h1>
          <p className="max-w-xl text-[14px] leading-relaxed text-muted">
            Every stop comes from our own curated database. Every drive is measured on the
            real road network. Nothing on the page is invented.
          </p>
          <div>
            <Link
              href="/plan"
              className="inline-flex items-center gap-2 rounded-xl bg-gold px-5 py-3 font-display text-[14px] font-semibold text-ink-950 transition hover:brightness-110"
            >
              Start planning →
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}

const MODES = [
  {
    href: "/plan",
    glyph: "◐",
    title: "Plan by interest",
    body: "Tell us what excites you",
    tone: "bg-teal-soft text-teal ring-teal/30",
  },
  {
    href: "/plan/location",
    glyph: "◇",
    title: "Plan by location",
    body: "Anywhere you already have in mind",
    tone: "bg-ink-700 text-cream ring-line",
  },
  {
    href: "/plan/district",
    glyph: "▵",
    title: "Plan by district",
    body: "One district, end to end",
    tone: "bg-gold-soft text-gold ring-gold/30",
  },
];

/** All three are live as of Phase 2 — the "coming soon" labels are gone. */
function Modes() {
  return (
    <section className="mt-5 grid gap-3 sm:grid-cols-3">
      {MODES.map((mode) => (
        <Link
          key={mode.href}
          href={mode.href}
          className="panel group flex items-center gap-3.5 p-4 transition hover:border-gold/50 hover:bg-ink-800"
        >
          <span
            aria-hidden
            className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-[17px] ring-1 ${mode.tone}`}
          >
            {mode.glyph}
          </span>
          <span className="min-w-0">
            <span className="block font-display text-[15px] font-semibold">{mode.title}</span>
            <span className="block truncate text-[12px] text-muted">{mode.body}</span>
          </span>
          <span
            aria-hidden
            className="ml-auto text-muted-dim transition group-hover:text-gold"
          >
            →
          </span>
        </Link>
      ))}
    </section>
  );
}

const MONTH_ABBR = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/**
 * The getaway row.
 *
 * One card per district we can actually plan. Where the reference shows
 * "4.8 (1.2K) · from ₹4,999", this shows the published place count and the months
 * that are open — both queryable facts. Phase 1 has one district, so the row also
 * says so rather than padding itself out with places we have not seeded.
 */
function Getaways({ districts }: { districts: District[] }) {
  if (districts.length === 0) return null;

  return (
    <section id="districts" className="mt-8">
      <SectionHead
        title="Where we can plan today"
        note={`${districts.length} district${districts.length === 1 ? "" : "s"} live`}
      />
      {/* The pending card counts towards the row, so two live districts already
          fill three columns. With one, two columns look deliberate. */}
      <div
        className={`mt-4 grid gap-4 sm:grid-cols-2 ${
          districts.length > 1 ? "lg:grid-cols-3" : ""
        }`}
      >
        {districts.map((district, index) => (
          <DistrictCard key={district.slug} district={district} priority={index === 0} />
        ))}
        <PendingDistrictCard />
      </div>
    </section>
  );
}

function DistrictCard({ district, priority }: { district: District; priority: boolean }) {
  const photo = firstPhoto(district.media);
  const months = district.open_months ?? [];
  return (
    <Link
      href="/plan/district"
      className="panel group overflow-hidden transition hover:border-gold/50"
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
          className="absolute inset-0 bg-gradient-to-t from-ink-950 via-ink-950/30 to-transparent"
        />
        <div className="absolute inset-x-4 bottom-3">
          <h3 className="font-display text-[18px] font-semibold">{district.name}</h3>
          <p className="eyebrow mt-0.5 text-cream/60">
            {district.published_places} places &amp; activities
          </p>
        </div>
      </div>
      <div className="space-y-2.5 p-4">
        <div className="flex flex-wrap gap-1.5">
          {district.top_interests.map((label) => (
            <span
              key={label}
              className="rounded-full bg-ink-700 px-2.5 py-0.5 text-[11px] text-muted"
            >
              {label}
            </span>
          ))}
        </div>
        {months.length > 0 && months.length < 12 && (
          <p className="text-[11.5px] text-muted-dim">
            <span className="text-teal">Open</span>{" "}
            {months.map((m) => MONTH_ABBR[m - 1]).join(" · ")}
          </p>
        )}
        {photo && (
          <p className="truncate text-[10px] text-muted-dim/80">
            Photo © {photo.artist} · {photo.license}
          </p>
        )}
      </div>
    </Link>
  );
}

/**
 * The honest counterpart to the reference's five-card carousel.
 *
 * A dashed empty slot says "more districts need seed data" instead of listing
 * Coorg, Gokarna and Hampi as if we could plan them. Whoever seeds the next
 * district makes this card disappear.
 */
function PendingDistrictCard() {
  return (
    <div className="panel flex flex-col justify-center gap-2 border-dashed p-5">
      <p className="eyebrow text-muted-dim">Not yet</p>
      <p className="font-display text-[15px] font-semibold text-muted">
        Coorg, Gokarna, Hampi and the rest
      </p>
      <p className="text-[12px] leading-relaxed text-muted-dim">
        Each district needs its places, stays and seasons compiled and fact-checked before
        it can be planned. The engine is ready for them; the data is not.
      </p>
    </div>
  );
}

/**
 * Preset briefs, replacing the reference's "AI Trip Inspiration" row.
 *
 * Each one is a real interest tag with a real photograph of a place carrying it,
 * linking into the wizard with that interest preselected. Nothing here is
 * generated — the "inspiration" is our own taxonomy, shown honestly.
 */
function Inspiration({ interests }: { interests: Interest[] }) {
  const withPhotos = interests.filter((i) => i.photo).slice(0, 4);
  if (withPhotos.length === 0) return null;

  return (
    <section className="mt-8">
      <SectionHead title="Start from an idea" note="preselects the interest" />
      <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {withPhotos.map((interest) => (
          <Link
            key={interest.slug}
            href={`/plan?interest=${interest.slug}`}
            className="panel group relative h-40 overflow-hidden transition hover:border-gold/50"
          >
            <PhotoFrame
              photo={interest.photo}
              alt=""
              variant="cover"
              rounded="rounded-none"
              sizes="(max-width: 640px) 100vw, 25vw"
              showCredit={false}
            />
            <div
              aria-hidden
              className="absolute inset-0 bg-gradient-to-t from-ink-950 via-ink-950/45 to-transparent"
            />
            <div className="absolute inset-x-4 bottom-3">
              <h3 className="font-display text-[16px] font-semibold">{interest.label}</h3>
              <p className="line-clamp-2 text-[11.5px] leading-snug text-muted">
                {interest.description}
              </p>
              <p className="eyebrow mt-1 text-gold opacity-0 transition group-hover:opacity-100">
                Plan this →
              </p>
            </div>
          </Link>
        ))}
      </div>
    </section>
  );
}

function Gallery({ photos }: { photos: Photo[] }) {
  if (photos.length < 5) return null;
  // Five tiles fill a 6x2 grid exactly. Six left a third row two-thirds empty.
  //
  // The mosaic is desktop-only: at 390px a sixth of the width is 60px, which made
  // every caption truncate to a single letter. On a phone it becomes two even
  // columns, where the place names actually fit.
  const spans = [
    "sm:col-span-3 sm:row-span-2",
    "sm:col-span-2",
    "sm:col-span-1",
    "sm:col-span-2",
    "sm:col-span-1",
  ];

  return (
    <section id="gallery" className="mt-8">
      <SectionHead title="What you would actually see" note="Wikimedia Commons · CC" />
      <div className="mt-4 grid auto-rows-[132px] grid-cols-2 gap-2.5 sm:grid-cols-6">
        {photos.map((photo, index) => (
          <figure
            key={photo.title}
            className={`relative overflow-hidden rounded-xl border border-line ${
              spans[index] ?? "sm:col-span-2"
            }`}
          >
            <PhotoFrame
              photo={photo}
              alt={photo.caption ?? ""}
              variant="cover"
              rounded="rounded-none"
              sizes="(max-width: 640px) 50vw, 33vw"
              showCredit={false}
            />
            <div
              aria-hidden
              className="absolute inset-0 bg-gradient-to-t from-ink-950/90 via-transparent to-transparent"
            />
            <figcaption className="absolute inset-x-3 bottom-2">
              <span className="block truncate font-display text-[13px] font-semibold">
                {photo.caption}
              </span>
              <span className="block truncate text-[9.5px] text-muted-dim">
                © {photo.artist} · {photo.license}
              </span>
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  );
}

/**
 * The footer strip, rewritten.
 *
 * The reference promises "AI-Powered Planning", "Secure & Reliable" and
 * "24/7 Support". Two of those are not true here and the third is marketing, so
 * each slot states something that is true and checkable instead — including the
 * limitations, which belong on the front page rather than in a settings screen.
 */
function Truths() {
  const items = [
    {
      title: "Curated, never invented",
      body: "The composer may only choose from places in our database. Enforced in code, with a test.",
    },
    {
      title: "Real road distances",
      body: "Driving times come from OSRM on OpenStreetMap data, not from an estimate or a guess.",
    },
    {
      title: "Credited photography",
      body: "Every image is Creative Commons, with its author and licence shown on the image.",
    },
    {
      title: "Honest about gaps",
      body: "Unverified data, permits, late finishes and long drives are flagged on the itinerary.",
    },
  ];
  return (
    <section className="mt-10 mb-4 grid gap-3 border-t border-line pt-6 sm:grid-cols-2 lg:grid-cols-4">
      {items.map((item) => (
        <div key={item.title}>
          <p className="font-display text-[13.5px] font-semibold text-gold">{item.title}</p>
          <p className="mt-1 text-[11.5px] leading-relaxed text-muted-dim">{item.body}</p>
        </div>
      ))}
    </section>
  );
}

function SectionHead({ title, note }: { title: string; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <h2 className="font-display text-[20px] font-bold">{title}</h2>
      {note && <span className="eyebrow text-muted-dim">{note}</span>}
    </div>
  );
}
