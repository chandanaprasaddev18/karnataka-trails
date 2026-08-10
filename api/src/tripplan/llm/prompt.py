"""Prompt construction for the composer.

Two things matter here.

**What the prompt contains.** Only the brief and a compact candidate list: ref,
name, kind, one-line summary, locality, duration, cost band. No UUIDs (wasteful),
no coordinates, no distances, no prices in rupees, no contact details. If the
model never sees a fact, it cannot restate one incorrectly — and it has no reason
to try, because the code fills those in afterwards.

**What the prompt asks for.** Judgement: grouping by geography, pacing across a
day, thematic coherence, and a reason per stop. It explicitly does *not* ask for
ordering within a day, because the routing provider decides that and the model's
ordering would be silently overwritten — asking for work that gets discarded
wastes tokens and invites the model to reason about distances it cannot see.
"""

from __future__ import annotations

from tripplan.domain.models import Candidate, CandidateSet, DraftItinerary, TripBrief

SYSTEM_PROMPT = """\
You are an itinerary composer for a Karnataka travel planner.

You are given a trip brief and a list of CANDIDATE places, stays and activities \
that have already been retrieved from a curated database and filtered for the \
traveller's interests, budget and travel month. Your job is to group them into \
days and explain the choices.

Hard rules:
- Use ONLY the refs in the candidate list. Never invent a place, and never name \
somewhere that is not in the list. A ref you did not receive will be rejected.
- Put place and activity refs in a day's `items`. Put a stay ref in `stay_ref`. \
Never put a stay in `items`.
- Produce exactly the requested number of days, numbered from 1.
- Do not repeat a place across days.
- Keep each day geographically coherent: stops on the same day should be near \
each other, and consecutive days should not criss-cross the district.

What you are judged on:
- Pacing. A day of four temples is worse than two temples and a viewpoint. Vary \
the texture of a day, and leave a hard trek room to breathe.
- Honest sequencing. Day 1 begins with a long drive from the origin, so it \
should be lighter. A demanding trek belongs on a morning, not after a transfer.
- Reasons. `why_chosen` should say something specific about the stop's place in \
the day, not restate its summary.

Do NOT write distances, driving times, prices, opening hours or phone numbers. \
You do not have that data, and the system fills it in from the database. Any \
number you invent will be discarded and counted against you."""


def _line(candidate: Candidate) -> str:
    """One compact line per candidate. Terse on purpose: this list dominates the prompt."""
    bits = [f"{candidate.ref}", candidate.name, f"({candidate.region.name})"]
    if candidate.duration_minutes:
        bits.append(f"{candidate.duration_minutes // 60}h{candidate.duration_minutes % 60:02d}")
    if candidate.cost_band:
        bits.append(f"cost{candidate.cost_band}")
    if candidate.difficulty:
        bits.append(f"difficulty{candidate.difficulty}")
    detail_type = (
        candidate.detail.get("place_type")
        or candidate.detail.get("activity_type")
        or candidate.detail.get("stay_type")
    )
    if detail_type:
        bits.append(str(detail_type))
    if candidate.tags:
        bits.append("tags=" + ",".join(candidate.tags))
    head = " · ".join(str(b) for b in bits)
    return f"- {head}\n  {candidate.summary}"


def build_user_prompt(
    brief: TripBrief,
    candidates: CandidateSet,
    *,
    day_activity_minutes: int,
    repair_of: DraftItinerary | None = None,
    violations: str | None = None,
) -> str:
    sections: list[str] = []

    sections.append(
        "TRIP BRIEF\n"
        f"- days: {brief.days}\n"
        f"- travellers: {brief.party_size}\n"
        f"- interests: {', '.join(brief.tag_slugs)}\n"
        f"- budget band: {brief.budget_band} of 5\n"
        f"- starting from: {brief.origin.label}\n"
        f"- travel month: {brief.travel_month}\n"
        f"- usable activity time per day: about {day_activity_minutes // 60} hours, "
        "before driving"
    )

    sections.append(
        "CANDIDATE PLACES (use these refs in `items`)\n"
        + "\n".join(_line(c) for c in candidates.places)
    )
    if candidates.activities:
        sections.append(
            "CANDIDATE ACTIVITIES (use these refs in `items`)\n"
            + "\n".join(_line(c) for c in candidates.activities)
        )
    if candidates.stays:
        sections.append(
            "CANDIDATE STAYS (use these refs in `stay_ref` only)\n"
            + "\n".join(_line(c) for c in candidates.stays)
        )
    else:
        sections.append("CANDIDATE STAYS\n(none available within budget — set stay_ref to null)")

    if repair_of is not None and violations:
        # A repair attempt quotes the specific failures. "That was wrong, try
        # again" gets another guess; naming the invalid ref gets a correction.
        sections.append(
            "YOUR PREVIOUS ATTEMPT WAS REJECTED\n"
            "Fix exactly these problems and return the whole itinerary again:\n"
            f"{violations}\n\n"
            "Remember: every ref must appear in the candidate lists above."
        )

    return "\n\n".join(sections)
