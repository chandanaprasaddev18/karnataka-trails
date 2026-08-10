import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trip Planner — Karnataka",
  description: "Day-by-day itineraries built from a curated database of places, stays and guides.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-paper text-ink">
        <header className="border-b border-line bg-card/70 backdrop-blur">
          <div className="mx-auto flex max-w-4xl items-baseline justify-between px-5 py-4">
            <Link href="/" className="text-lg font-semibold tracking-tight">
              Trip Planner
            </Link>
            <span className="text-xs text-muted">Chikkamagaluru · Phase 1</span>
          </div>
        </header>
        <main className="mx-auto max-w-4xl px-5 py-8">{children}</main>
        <footer className="mx-auto max-w-4xl px-5 pb-10 pt-4 text-xs text-muted">
          Places, stays and guides come from our own curated database. Travel times are static
          estimates in this phase — confirm timings, prices and permits before travelling.
        </footer>
      </body>
    </html>
  );
}
