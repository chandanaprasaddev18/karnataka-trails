"""Terminal rendering for an itinerary.

Exists so step 5 is reviewable — and so the deterministic and LLM outputs can be
read side by side — before any frontend exists.
"""

from __future__ import annotations

from tripplan.domain.models import Itinerary, Money


def rupees(money: Money | None) -> str:
    if money is None:
        return "—"
    if money.min_paise == money.max_paise:
        return f"₹{money.min_paise // 100:,}"
    # En dash is the correct glyph for a numeric range.
    return f"₹{money.min_paise // 100:,}–{money.max_paise // 100:,}"  # noqa: RUF001


def _hours(minutes: int) -> str:
    return f"{minutes // 60}h{minutes % 60:02d}m"


def render_text(itinerary: Itinerary, *, width: int = 78) -> str:
    rule = "═" * width
    thin = "─" * width
    out: list[str] = [rule, itinerary.summary.title.upper(), rule]

    brief = itinerary.brief
    out.append(
        f"{brief.days} days · {brief.party_size} people · budget band {brief.budget_band}"
        f" · from {brief.origin.label} · {brief.district.name}"
    )
    out.append("interests: " + ", ".join(t.label for t in brief.interests))
    out.append(
        f"composer: {itinerary.composer}"
        + (f" ({itinerary.llm_model})" if itinerary.llm_model else "")
        + f" · schema v{itinerary.schema_version}"
    )
    if itinerary.candidate_set_hash:
        out.append(f"candidate set: {itinerary.candidate_set_hash[:12]}")
    out.append("")
    if itinerary.summary.narrative:
        out.append(itinerary.summary.narrative)
        out.append("")
    out.append(
        f"total travel: {itinerary.summary.total_distance_km:,.0f} km · "
        f"{_hours(itinerary.summary.total_travel_minutes)} · "
        f"estimated cost {rupees(itinerary.summary.estimated_cost)} (all people, "
        "excludes transport and meals)"
    )

    for day in itinerary.days:
        out.append("")
        out.append(thin)
        header = f"DAY {day.day_number} · {day.title}"
        if day.travel:
            header += (
                f"   [{day.travel.distance_km:,.0f} km, "
                f"{_hours(day.travel.duration_minutes)} driving]"
            )
        out.append(header)
        out.append(thin)
        if day.narrative:
            out.append(day.narrative)
            out.append("")

        for item in day.items:
            leg = ""
            if item.leg_from_previous:
                leg = (
                    f"  ({item.leg_from_previous.distance_km:,.0f} km, "
                    f"{item.leg_from_previous.duration_minutes} min"
                    f", {item.leg_from_previous.source})"
                )
            out.append(f"  {item.start_time_estimate or '--:--'}  {item.name}{leg}")
            out.append(f"          {item.summary}")
            meta: list[str] = []
            if item.duration_minutes:
                meta.append(_hours(item.duration_minutes))
            if item.cost:
                meta.append(rupees(item.cost) + " pp")
            if item.detail.get("requires_permit"):
                meta.append("PERMIT REQUIRED")
            if meta:
                out.append("          " + " · ".join(meta))
            if item.why_chosen:
                out.append(f"          why: {item.why_chosen}")
            for guide in item.guides:
                langs = "/".join(guide.languages) if guide.languages else "—"
                out.append(f"          guide: {guide.name} ({langs})")

        if day.stay:
            out.append("")
            price = f" · {rupees(day.stay.per_night)}/night" if day.stay.per_night else ""
            meals = " · meals included" if day.stay.meals_included else ""
            out.append(f"  STAY  {day.stay.name} ({day.stay.stay_type}){price}{meals}")
        else:
            out.append("")
            out.append("  STAY  none selected — arrange your own")

    if itinerary.return_leg:
        out.append("")
        out.append(thin)
        out.append(
            f"RETURN  to {itinerary.return_leg.to.label} · "
            f"{itinerary.return_leg.distance_km:,.0f} km · "
            f"{_hours(itinerary.return_leg.duration_minutes)}"
        )

    if itinerary.summary.warnings:
        out.append("")
        out.append(rule)
        out.append("WARNINGS")
        out.append(rule)
        for warning in itinerary.summary.warnings:
            where = f"day {warning.day_number}: " if warning.day_number else ""
            out.append(f"  ! [{warning.code}] {where}{warning.message}")

    return "\n".join(out)
