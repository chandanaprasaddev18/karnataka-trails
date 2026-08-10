"""Stage 3 — where "the model may not invent a place" is actually enforced.

A prompt instruction is a request. This module is the guarantee: every reference
a composer emits must resolve inside the candidate set that was handed to it, or
the draft is rejected. Nothing downstream trusts a composer's output.

Violations are split by severity because the two need different handling:

* ``fatal``    — the draft is unusable. Trigger a repair round-trip, then the
                 deterministic fallback. An unknown ref is always fatal.
* ``advisory`` — the draft is usable but strained (a long travel day, a thin
                 day). These become client-visible warnings instead of being
                 silently swallowed.

The deterministic composer is validated too. It should never fail — and if a
change to it ever does, the tests catch it here rather than in production.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from tripplan.domain.models import CandidateSet, DraftItinerary, TripBrief
from tripplan.observability.logging import get_logger

log = get_logger(__name__)

Severity = Literal["fatal", "advisory"]

ViolationCode = Literal[
    "unknown_ref",
    "wrong_kind_for_stay",
    "wrong_kind_for_item",
    "day_count_mismatch",
    "duplicate_day_number",
    "missing_day_number",
    "repeated_poi",
    "empty_day",
    "no_stay",
    "over_time_budget",
]


class Violation(BaseModel):
    code: ViolationCode
    severity: Severity
    message: str
    ref: str | None = None
    day_number: int | None = None

    def render(self) -> str:
        where = f" (day {self.day_number})" if self.day_number else ""
        return f"[{self.code}]{where} {self.message}"


class ValidationResult(BaseModel):
    violations: list[Violation] = Field(default_factory=list)

    @property
    def fatal(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "fatal"]

    @property
    def advisory(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "advisory"]

    @property
    def ok(self) -> bool:
        return not self.fatal

    def repair_brief(self) -> str:
        """A precise, quotable list of what to fix — fed back on the repair attempt.

        Specific violations beat "that was wrong, try again": the model needs to
        know which ref was invalid and which day was short.
        """
        return "\n".join(f"- {v.render()}" for v in self.fatal)


def validate(
    draft: DraftItinerary,
    candidates: CandidateSet,
    brief: TripBrief,
    *,
    day_activity_minutes: int,
) -> ValidationResult:
    result = ValidationResult()
    by_ref = candidates.by_ref()
    valid_refs = set(by_ref)

    # --- the core guarantee: every ref must exist ---------------------------
    for ref in sorted(draft.referenced_refs()):
        if ref not in valid_refs:
            result.violations.append(
                Violation(
                    code="unknown_ref",
                    severity="fatal",
                    ref=ref,
                    message=(
                        f"{ref!r} is not in the candidate set. Only the listed "
                        "refs may be used; do not introduce a place by name."
                    ),
                )
            )

    # --- day structure -----------------------------------------------------
    numbers = [d.day_number for d in draft.days]
    if len(draft.days) != brief.days:
        result.violations.append(
            Violation(
                code="day_count_mismatch",
                severity="fatal",
                message=f"expected exactly {brief.days} day(s), got {len(draft.days)}",
            )
        )
    duplicates = {n for n in numbers if numbers.count(n) > 1}
    for number in sorted(duplicates):
        result.violations.append(
            Violation(
                code="duplicate_day_number",
                severity="fatal",
                day_number=number,
                message=f"day {number} appears more than once",
            )
        )
    for expected in range(1, brief.days + 1):
        if expected not in numbers:
            result.violations.append(
                Violation(
                    code="missing_day_number",
                    severity="fatal",
                    day_number=expected,
                    message=f"day {expected} is missing; days must be 1..{brief.days}",
                )
            )

    # --- per-day content ---------------------------------------------------
    seen_pois: set[str] = set()
    for day in draft.days:
        if day.stay_ref and day.stay_ref in valid_refs:
            stay = by_ref[day.stay_ref]
            if stay.kind != "stay":
                result.violations.append(
                    Violation(
                        code="wrong_kind_for_stay",
                        severity="fatal",
                        ref=day.stay_ref,
                        day_number=day.day_number,
                        message=f"{day.stay_ref} is a {stay.kind}, not a stay",
                    )
                )
        elif not day.stay_ref:
            # Advisory, not fatal: a district may genuinely have no stay within
            # budget near a cluster, and refusing the whole itinerary over that
            # is worse than telling the traveller to sort out their own bed.
            result.violations.append(
                Violation(
                    code="no_stay",
                    severity="advisory",
                    day_number=day.day_number,
                    message="no stay was selected for this night",
                )
            )

        if not day.items:
            # A day with no stops but a bed is a legitimate arrival or transit
            # day — Chikkamagaluru is 200+ km from every plausible origin, so a
            # long first drive is real. A day with neither stops nor a bed is
            # just a hole in the itinerary.
            has_bed = bool(day.stay_ref)
            result.violations.append(
                Violation(
                    code="empty_day",
                    severity="advisory" if has_bed else "fatal",
                    day_number=day.day_number,
                    message=(
                        "no stops — the day is consumed by travel"
                        if has_bed
                        else "a day must contain at least one place or activity, or a stay"
                    ),
                )
            )

        spent = 0
        for item in day.items:
            if item.ref not in valid_refs:
                continue  # already reported as unknown_ref
            candidate = by_ref[item.ref]
            if candidate.kind == "stay":
                result.violations.append(
                    Violation(
                        code="wrong_kind_for_item",
                        severity="fatal",
                        ref=item.ref,
                        day_number=day.day_number,
                        message=(
                            f"{item.ref} is a stay and belongs in stay_ref, not in the day's items"
                        ),
                    )
                )
                continue
            if not candidate.is_repeatable:
                if item.ref in seen_pois:
                    result.violations.append(
                        Violation(
                            code="repeated_poi",
                            severity="fatal",
                            ref=item.ref,
                            day_number=day.day_number,
                            message=(
                                f"{candidate.name} already appears on an earlier day "
                                "and is not marked repeatable"
                            ),
                        )
                    )
                seen_pois.add(item.ref)
            spent += candidate.duration_minutes or 0

        if spent > day_activity_minutes:
            result.violations.append(
                Violation(
                    code="over_time_budget",
                    severity="advisory",
                    day_number=day.day_number,
                    message=(
                        f"{spent} minutes of activity exceeds the "
                        f"{day_activity_minutes}-minute day budget"
                    ),
                )
            )

    if result.violations:
        log.info(
            "engine.validate",
            fatal=len(result.fatal),
            advisory=len(result.advisory),
            codes=sorted({v.code for v in result.violations}),
        )
    return result
