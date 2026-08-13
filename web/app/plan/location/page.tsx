import { PlanWizard } from "@/components/PlanWizard";

/** Plan by location: a radius around a place or town we hold. */
export default function PlanByLocationPage() {
  return (
    <main>
      <PlanWizard mode="location" />
    </main>
  );
}
