import { PlanWizard } from "@/components/PlanWizard";

/**
 * Plan by location: a radius around a place or town we hold.
 *
 * `?anchor=<slug>` is how the global search hands off. searchParams is a Promise
 * in Next 16 — see web/AGENTS.md.
 */
export default async function PlanByLocationPage({
  searchParams,
}: PageProps<"/plan/location">) {
  const params = await searchParams;
  const anchor = typeof params.anchor === "string" ? params.anchor : undefined;
  return (
    <main>
      <PlanWizard mode="location" initialAnchor={anchor} />
    </main>
  );
}
