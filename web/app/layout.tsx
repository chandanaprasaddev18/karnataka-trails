import type { Metadata } from "next";
import { Fraunces, IBM_Plex_Mono, Plus_Jakarta_Sans } from "next/font/google";
import Link from "next/link";
import "./globals.css";

/*
 * Fonts are self-hosted by next/font at build time rather than linked from
 * Google's CDN as the mockup does: no third-party request at runtime, no
 * layout shift, and the app keeps working offline.
 *
 * The mockup specifies General Sans, which is not on Google Fonts. Plus Jakarta
 * Sans is the closest match in the same geometric-humanist register.
 */
const fraunces = Fraunces({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-fraunces",
  display: "swap",
});

const jakarta = Plus_Jakarta_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-jakarta",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Karnataka Trails — trip planner",
  description:
    "Day-by-day itineraries built from a curated database of places, stays and guides in Karnataka.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${fraunces.variable} ${jakarta.variable} ${plexMono.variable}`}>
      <body className="min-h-screen bg-cream text-ink">
        <Navbar />
        {children}
        <Footer />
      </body>
    </html>
  );
}

function Navbar() {
  return (
    <header className="bg-navy">
      <nav className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-4 sm:px-8">
        <Link href="/" className="flex items-center gap-2.5">
          <span aria-hidden className="h-2.5 w-2.5 rounded-full bg-marigold" />
          <span className="eyebrow text-cream">Karnataka Trails</span>
        </Link>
        <div className="flex items-center gap-5 text-[13.5px] text-muted-dim sm:gap-8">
          <Link href="/" className="text-cream">
            Explore
          </Link>
          <Link href="/plan" className="transition hover:text-cream">
            Plan
          </Link>
          {/* Deliberately not links. Saved trips and guide booking are later
              phases, and a nav item that 404s is worse than one that says so. */}
          <span className="hidden cursor-not-allowed text-navy-line sm:inline" title="Phase 4">
            Guides
          </span>
        </div>
      </nav>
    </header>
  );
}

function Footer() {
  return (
    <footer className="border-t border-line bg-cream">
      <div className="mx-auto max-w-6xl space-y-2 px-5 py-8 text-xs text-muted sm:px-8">
        <p>
          Places, stays and guides come from our own curated database — nothing here is
          invented. Travel times are static estimates in this phase; confirm timings, prices
          and permits before travelling.
        </p>
        <p>
          Photographs are from Wikimedia Commons under Creative Commons licences and are
          credited on each image.
        </p>
      </div>
    </footer>
  );
}
