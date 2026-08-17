import Link from "next/link";
import { fetchSpecialities } from "@/lib/api";
import type { MarketView, Speciality } from "@/lib/types";

/**
 * The marketplace — Phase 5.
 *
 * The requirement was hyperlocal products tagged by district and interest. This
 * page shows the half of that we can honestly ship: what each place is known for
 * producing, tagged to its region and to a `product_category` in the same
 * `interest_tags` vocabulary the planner uses.
 *
 * It lists no sellers, and it says so at the top rather than burying it. Every
 * vendor we hold is a development placeholder that the publish gate refuses,
 * because a vendor is someone a traveller might try to pay — inventing one could
 * send money to a business that does not exist. The vendor and product path is
 * built and tested; the moment a real seller consents to being listed, their
 * products appear under the speciality they belong to.
 *
 * Server-rendered: this is content, and the honest headline is a number the server
 * already knows.
 */
const MONTHS = [
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

export default async function MarketplacePage() {
  let view: MarketView | null = null;
  let unreachable = false;
  try {
    view = await fetchSpecialities({ district: "chikkamagaluru" });
  } catch {
    unreachable = true;
  }

  const specialities = view?.specialities ?? [];
  const stats = view?.stats;

  return (
    <main className="mx-auto max-w-[1200px] px-4 py-6 sm:px-6 lg:px-8">
      <p className="eyebrow text-gold">Marketplace</p>
      <h1 className="mt-2 font-display text-[30px] font-bold">What to bring home</h1>
      <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-muted">
        What each part of the district actually produces, and when it is worth buying. Tagged by
        region and by category, from the same vocabulary the planner uses for interests.
      </p>

      {unreachable && (
        <p className="mt-5 rounded-xl border border-rust/40 bg-rust-soft p-4 text-[13px] text-rust">
          Could not reach the API. Start it with <code>make api</code>.
        </p>
      )}

      {stats && <SellerNotice stats={stats} />}

      <div className="mt-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {specialities.map((speciality) => (
          <SpecialityCard
            key={`${speciality.region_slug}-${speciality.category_slug}`}
            speciality={speciality}
          />
        ))}
      </div>

      {specialities.length === 0 && !unreachable && (
        <p className="panel mt-5 p-6 text-[13px] text-muted">
          No specialities recorded yet. They live in{" "}
          <code>api/seeds/&lt;district&gt;/specialities.yaml</code>.
        </p>
      )}

      <section className="mt-8 border-t border-line pt-6">
        <h2 className="font-display text-[17px] font-semibold">Why there is nothing to buy here</h2>
        <p className="mt-2 max-w-3xl text-[12.5px] leading-relaxed text-muted-dim">
          A shop needs sellers. Listing one means holding their name, their location and their
          contact details, and being reasonably sure a traveller who acts on it reaches the right
          person. We have not done that work for any trader in this district, and the alternative
          — plausible-looking invented listings — is the one thing this product refuses to do:
          it could send money or a phone call to a business that does not exist, or to a real
          person who never agreed to be listed.
        </p>
        <p className="mt-2 max-w-3xl text-[12.5px] leading-relaxed text-muted-dim">
          So the schema, the tagging, the publish gate and this page are all built and exercised
          against development placeholders that can never be shown. Adding a real vendor is a
          seed-file change and a review, not a code change.
        </p>
        <Link
          href="/plan"
          className="mt-4 inline-block rounded-xl border border-line px-4 py-2.5 text-[13px] text-muted transition hover:border-gold hover:text-gold"
        >
          Plan a trip through these places →
        </Link>
      </section>
    </main>
  );
}

function SellerNotice({ stats }: { stats: MarketView["stats"] }) {
  return (
    <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-line bg-ink-850 px-4 py-3">
      <Stat value={String(stats.specialities)} label="specialities recorded" />
      <Stat value={String(stats.categories)} label="categories" />
      <Stat value={String(stats.published_vendors)} label="sellers listed" tone="text-rust" />
      <p className="text-[11.5px] leading-snug text-muted-dim">
        {stats.withheld_vendors > 0
          ? `${stats.withheld_vendors} vendor record${stats.withheld_vendors === 1 ? "" : "s"} exist in the database and are withheld: they are development placeholders, not real businesses.`
          : "No vendor records held."}
      </p>
    </div>
  );
}

function Stat({ value, label, tone }: { value: string; label: string; tone?: string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className={`font-display text-[19px] font-bold ${tone ?? ""}`}>{value}</span>
      <span className="eyebrow text-muted-dim">{label}</span>
    </span>
  );
}

function SpecialityCard({ speciality }: { speciality: Speciality }) {
  const months = speciality.best_months;
  return (
    <article className="panel flex flex-col gap-2.5 p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="font-display text-[16px] font-semibold">{speciality.category_label}</h3>
        <span className="eyebrow shrink-0 text-gold">{speciality.region_name}</span>
      </div>
      <p className="text-[12.5px] leading-relaxed text-muted">{speciality.note}</p>

      {months.length > 0 && months.length < 12 && (
        <p className="text-[11.5px] text-teal">
          Best {months.map((m) => MONTHS[m - 1]).join(" · ")}
        </p>
      )}

      <div className="mt-auto flex items-center justify-between gap-2 border-t border-line pt-2.5">
        {/* Provenance per row, as everywhere else in this app: a claim about a
            place is only as good as where it came from. */}
        <span className="eyebrow text-muted-dim">
          {speciality.source.replace(/_/g, " ")} · confidence {speciality.data_confidence}/5
        </span>
        <span className="eyebrow text-muted-dim">
          {speciality.products.length > 0
            ? `${speciality.products.length} listed`
            : "no sellers listed"}
        </span>
      </div>
    </article>
  );
}
