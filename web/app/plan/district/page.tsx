import { PlanWizard } from "@/components/PlanWizard";

/**
 * Plan by district: the whole district, interests optional.
 *
 * `?district=<slug>` preselects one, which is how the home page's district cards
 * hand off — clicking "Mysuru" and landing on a form set to Chikkamagaluru is how
 * the dropped-district bug stayed invisible. searchParams is a Promise in Next 16.
 */
export default async function PlanByDistrictPage({
  searchParams,
}: PageProps<"/plan/district">) {
  const params = await searchParams;
  const district = typeof params.district === "string" ? params.district : undefined;
  return (
    <main>
      <PlanWizard mode="district" initialDistrict={district} />
    </main>
  );
}
