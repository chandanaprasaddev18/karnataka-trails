"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ApiError, fetchPlanStatus } from "@/lib/api";
import { ItineraryView } from "@/components/ItineraryView";
import { SUPPORTED_SCHEMA_VERSION, type PlanStatus } from "@/lib/types";

/**
 * Polls a plan request until the worker finishes.
 *
 * Generation is a multi-step background job, so the client waits rather than
 * blocking a request. The job reports which pipeline stage it is in, which is why
 * this can say "composing the days" instead of showing an unexplained spinner.
 */
const POLL_MS = 1500;
// ~90s. Long enough for an LLM compose plus a repair round-trip; past that,
// something is wrong and saying so beats spinning forever.
const MAX_POLLS = 60;

const STAGE_COPY: Record<string, string> = {
  retrieval: "Finding places that match your interests…",
  compose: "Composing the days…",
  validate: "Checking every place against our database…",
  route: "Working out the driving between stops…",
  assemble: "Putting the itinerary together…",
};

export function PlanProgress({ requestId }: { requestId: string }) {
  const [status, setStatus] = useState<PlanStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [timedOut, setTimedOut] = useState(false);
  const polls = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const next = await fetchPlanStatus(requestId);
        if (cancelled) return;
        setStatus(next);

        const finished = next.job.status === "succeeded" || next.job.status === "failed";
        if (finished) return;

        polls.current += 1;
        if (polls.current >= MAX_POLLS) {
          setTimedOut(true);
          return;
        }
        timer = setTimeout(poll, POLL_MS);
      } catch (caught: unknown) {
        if (cancelled) return;
        setError(
          caught instanceof ApiError && caught.status === 404
            ? "We have no record of that request."
            : "Lost contact with the API.",
        );
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [requestId]);

  if (error) {
    return (
      <Shell>
        <Notice title="Something went wrong" body={error} />
      </Shell>
    );
  }

  if (!status) {
    return (
      <Shell>
        <Skeleton message="Checking on your itinerary…" />
      </Shell>
    );
  }

  if (status.job.status === "failed") {
    return (
      <Shell>
        <Notice
          title="We could not build this itinerary"
          body={
            status.job.error_detail ??
            "The job failed without a reason, which is a bug on our side."
          }
          action={
            <Link href="/plan" className="underline">
              Try different choices
            </Link>
          }
        />
      </Shell>
    );
  }

  if (!status.itinerary) {
    if (timedOut) {
      return (
        <Shell>
          <Notice
            title="This is taking longer than it should"
            body="The job has not finished after 90 seconds. Check that the worker is running (`make worker`)."
          />
        </Shell>
      );
    }
    return (
      <Shell>
        <Skeleton
          message={
            (status.job.stage && STAGE_COPY[status.job.stage]) ??
            (status.job.status === "queued" ? "Waiting for a worker…" : "Working…")
          }
          attempt={status.job.attempts > 1 ? status.job.attempts : undefined}
        />
      </Shell>
    );
  }

  // A payload from a newer server than this build understands. Refusing to render
  // beats silently dropping fields the reader is relying on.
  if (status.itinerary.schema_version !== SUPPORTED_SCHEMA_VERSION) {
    return (
      <Shell>
        <Notice
          title="This itinerary needs a newer version of the app"
          body={`It was built with schema v${status.itinerary.schema_version}; this page understands v${SUPPORTED_SCHEMA_VERSION}. Reload to pick up the latest build.`}
        />
      </Shell>
    );
  }

  return <ItineraryView itinerary={status.itinerary} />;
}

/** Page padding for the non-itinerary states; ItineraryView renders full-bleed. */
function Shell({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto max-w-6xl px-5 py-10 sm:px-8">{children}</div>;
}

function Skeleton({ message, attempt }: { message: string; attempt?: number }) {
  return (
    <div className="space-y-6" aria-live="polite" aria-busy="true">
      <div className="flex items-center gap-3">
        <span className="h-2 w-2 animate-pulse rounded-full bg-marigold" />
        <p className="text-sm text-muted">{message}</p>
      </div>
      {attempt && (
        <p className="text-xs text-muted">Retrying after a hiccup (attempt {attempt}).</p>
      )}
      <div className="space-y-3">
        {[0, 1, 2].map((row) => (
          <div key={row} className="flex gap-4 rounded-2xl border border-line bg-card p-4">
            <div className="h-20 w-28 shrink-0 animate-pulse rounded-lg bg-line" />
            <div className="flex-1 space-y-2.5 pt-1">
              <div className="h-4 w-1/3 animate-pulse rounded bg-line" />
              <div className="h-3 w-2/3 animate-pulse rounded bg-line" />
              <div className="h-3 w-1/2 animate-pulse rounded bg-line" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Notice({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="space-y-2 rounded-2xl border border-terracotta/30 bg-terracotta-soft p-5">
      <h2 className="font-display font-semibold text-terracotta">{title}</h2>
      <p className="text-sm text-ink/80">{body}</p>
      {action && <div className="pt-1 text-sm text-terracotta">{action}</div>}
    </div>
  );
}
