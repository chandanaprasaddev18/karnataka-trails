import { PlanProgress } from "@/components/PlanProgress";

/**
 * `params` is a Promise in Next 16 (synchronous access was removed), and
 * `PageProps<'/route'>` is the generated helper that types it for this route.
 * Regenerate with `npx next typegen` if the route changes.
 */
export default async function ItineraryPage(props: PageProps<"/itinerary/[requestId]">) {
  const { requestId } = await props.params;
  return (
    <main>
      <PlanProgress requestId={requestId} />
    </main>
  );
}
