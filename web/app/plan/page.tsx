import { PlanWizard } from "@/components/PlanWizard";

/**
 * Plan by interest.
 *
 * `?interest=<slug>` preselects one, which is how the home page's idea cards hand
 * off. searchParams is a Promise in Next 16 — see web/AGENTS.md.
 */
export default async function PlanByInterestPage({
  searchParams,
}: PageProps<"/plan">) {
  const params = await searchParams;
  const interest = typeof params.interest === "string" ? params.interest : undefined;
  return (
    <main>
      <PlanWizard mode="interest" initialInterest={interest} />
    </main>
  );
}
