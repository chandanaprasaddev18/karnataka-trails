import { PlanWizard } from "@/components/PlanWizard";

/** Plan by district: the whole district, interests optional. */
export default function PlanByDistrictPage() {
  return (
    <main>
      <PlanWizard mode="district" />
    </main>
  );
}
