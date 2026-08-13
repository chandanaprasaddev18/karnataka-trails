import type { Metadata } from "next";
import { IBM_Plex_Mono, Instrument_Sans, Outfit } from "next/font/google";
import { AppShell } from "@/components/AppShell";
import "./globals.css";

/*
 * Fonts are self-hosted by next/font at build time rather than linked from
 * Google's CDN: no third-party request at runtime, no layout shift, and the app
 * keeps working offline.
 *
 * The display face changed with the theme. Fraunces (a serif) suited the editorial
 * cream layout; this one is a dark product dashboard, where headings are short,
 * heavy and tightly set. Outfit is geometric and reads as software rather than as
 * a magazine, which is what the reference design is doing.
 *
 * Instrument Sans stays for body copy — it holds up at the 11-13px sizes this UI
 * leans on heavily — and IBM Plex Mono stays for eyebrows and figures, where a
 * tabular, spaced face makes distances and times scannable.
 */
const display = Outfit({
  subsets: ["latin"],
  variable: "--font-display-sans",
  display: "swap",
});

const body = Instrument_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-body",
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
    "Day-by-day itineraries built from a curated database of places, stays and guides in Karnataka, routed on the real road network.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${body.variable} ${plexMono.variable}`}
    >
      <body className="min-h-screen bg-ink-950 text-cream">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
