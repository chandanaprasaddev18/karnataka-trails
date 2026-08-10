import { Wizard } from "@/components/Wizard";

export default function HomePage() {
  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h1 className="text-3xl font-semibold tracking-tight">Plan by interest</h1>
        <p className="max-w-2xl text-muted">
          Pick what you want out of the trip and we will build a day-by-day itinerary through
          Chikkamagaluru — routed from your starting point, with somewhere to sleep each night and a
          way home.
        </p>
      </section>
      <Wizard />
    </div>
  );
}
