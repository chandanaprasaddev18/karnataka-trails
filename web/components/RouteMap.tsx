import type { GeoPoint, ItineraryDay } from "@/lib/types";

/**
 * The day's drive, drawn from the road geometry OSRM returned.
 *
 * WHY AN SVG AND NOT A MAP LIBRARY. Tiles would mean a runtime dependency on a
 * third-party service for every page view — the exact problem that made us
 * download the Wikimedia photographs after their CDN started returning 429 — plus
 * a client-side library, an API key for most providers, and a component that
 * cannot render on the server. This draws the actual driven polyline with no
 * dependencies, no key, and no network call from the browser.
 *
 * WHAT IT IS NOT. There is no basemap, so this shows the SHAPE of the route and
 * the order of the stops, not the terrain around them. It is labelled as a shape
 * for that reason. The honest failure mode matters more than the pretty one: when
 * the provider returns no geometry, this component renders nothing rather than
 * joining the stops with straight lines, which in the Western Ghats would draw a
 * road that does not exist.
 *
 * Server-rendered: it is a pure function of the payload.
 */

const WIDTH = 520;
const HEIGHT = 300;
const PAD = 26;

export function RouteMap({ day, origin }: { day: ItineraryDay; origin?: GeoPoint }) {
  const line = day.route ?? [];
  if (line.length < 2) return null;

  const stops = day.items.map((item) => item.point);
  if (day.stay) stops.push(day.stay.point);

  // Everything shares one projection so the polyline and the pins agree.
  const all: [number, number][] = [
    ...line,
    ...stops.map((p): [number, number] => [p.lat, p.lon]),
  ];
  if (origin) all.push([origin.lat, origin.lon]);

  const lats = all.map(([lat]) => lat);
  const lons = all.map(([, lon]) => lon);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);

  // Equirectangular with a cos(lat) correction on longitude. At this latitude and
  // over ~200 km the distortion is invisible, and it keeps the drawing honest
  // about proportions without pulling in a projection library.
  const midLat = (minLat + maxLat) / 2;
  const lonScale = Math.cos((midLat * Math.PI) / 180);
  const spanLat = Math.max(maxLat - minLat, 0.02);
  const spanLon = Math.max((maxLon - minLon) * lonScale, 0.02);
  const scale = Math.min((WIDTH - PAD * 2) / spanLon, (HEIGHT - PAD * 2) / spanLat);

  // Centre whichever axis has room left over, so a mostly north-south route does
  // not hug the left edge.
  const offsetX = (WIDTH - spanLon * scale) / 2;
  const offsetY = (HEIGHT - spanLat * scale) / 2;

  const project = ([lat, lon]: [number, number]): [number, number] => [
    offsetX + (lon - minLon) * lonScale * scale,
    // SVG y grows downwards; latitude grows upwards.
    HEIGHT - offsetY - (lat - minLat) * scale,
  ];

  const path = line.map(project).map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`);

  return (
    <figure className="panel overflow-hidden">
      <div className="flex items-baseline justify-between px-4 pt-3.5">
        <p className="eyebrow text-gold">Day {day.day_number} route</p>
        <p className="eyebrow text-muted-dim">shape only · no basemap</p>
      </div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="mt-2 h-auto w-full"
        role="img"
        aria-label={`The driven route for day ${day.day_number}, ${day.items.length} stops`}
      >
        <defs>
          <linearGradient id={`road-${day.day_number}`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#f0b429" />
            <stop offset="100%" stopColor="#e2683c" />
          </linearGradient>
        </defs>

        {/* The road, twice: a wide soft pass for glow, a thin one on top. */}
        <polyline
          points={path.join(" ")}
          fill="none"
          stroke={`url(#road-${day.day_number})`}
          strokeWidth={7}
          strokeOpacity={0.16}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <polyline
          points={path.join(" ")}
          fill="none"
          stroke={`url(#road-${day.day_number})`}
          strokeWidth={2.2}
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {stops.map((point, index) => {
          const [x, y] = project([point.lat, point.lon]);
          const isStay = Boolean(day.stay) && index === stops.length - 1;
          return (
            <g key={`${point.lat}-${point.lon}-${index}`}>
              <circle cx={x} cy={y} r={9} fill="#0a1018" stroke="#1f2c45" />
              <circle cx={x} cy={y} r={4.5} fill={isStay ? "#2bb3a3" : "#f0b429"} />
              <text
                x={x}
                y={y - 13}
                textAnchor="middle"
                className="font-mono"
                fontSize={9.5}
                fill="#94a3bf"
              >
                {isStay ? "bed" : index + 1}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="px-4 pb-3.5 text-[11px] leading-snug text-muted-dim">
        Drawn from the road geometry the router returned, so the bends are real. Distances
        and times come from the same measurement.
      </figcaption>
    </figure>
  );
}
