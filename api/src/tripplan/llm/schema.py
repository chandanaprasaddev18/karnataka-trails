"""The composer's output schema — a strict subset of the itinerary.

This schema is the second half of the "the model cannot state a fact" guarantee.
The first half is that `DraftItinerary` has no factual fields; this is the wire
format that gets *enforced*, with ``additionalProperties: false`` everywhere so a
provider cannot smuggle an extra key through.

Note what the model is asked for: which refs, in which day, with a title, a
narrative and a one-line rationale. That is judgement — pacing, theme, why this
pairs with that. Everything else is arithmetic or lookup, and is done in code.
"""

from __future__ import annotations

from typing import Any

TOOL_NAME = "emit_itinerary"

TOOL_DESCRIPTION = (
    "Return the day-by-day itinerary. Select only from the candidate refs given "
    "in the prompt. Do not name any place that is not in that list."
)


def itinerary_tool_schema(days: int) -> dict[str, Any]:
    """JSON Schema for the composer's output, pinned to the requested day count.

    Encoding `days` into `minItems`/`maxItems` lets the provider's own validator
    reject a wrong-length itinerary before it costs us a round-trip.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "narrative", "days"],
        "properties": {
            "title": {
                "type": "string",
                "description": "A short, specific title for the whole trip. No emoji.",
                "maxLength": 120,
            },
            "narrative": {
                "type": "string",
                "description": (
                    "Two or three sentences on the shape of the trip and why it is "
                    "ordered this way. Do not restate distances or prices."
                ),
                "maxLength": 800,
            },
            "days": {
                "type": "array",
                "minItems": days,
                "maxItems": days,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["day_number", "title", "narrative", "stay_ref", "items"],
                    "properties": {
                        "day_number": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": days,
                        },
                        "title": {"type": "string", "maxLength": 120},
                        "narrative": {"type": "string", "maxLength": 600},
                        "stay_ref": {
                            "type": ["string", "null"],
                            "description": (
                                "A stay ref (S-prefixed) for that night, or null if none "
                                "of the offered stays is a sensible base for this day."
                            ),
                        },
                        "items": {
                            "type": "array",
                            "minItems": 0,
                            "maxItems": 6,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["ref", "why_chosen"],
                                "properties": {
                                    "ref": {
                                        "type": "string",
                                        "description": (
                                            "A place (P) or activity (A) ref from the "
                                            "candidate list. Never a stay."
                                        ),
                                    },
                                    "why_chosen": {
                                        "type": "string",
                                        "description": (
                                            "One sentence on why this stop belongs here, "
                                            "in this order. No prices or distances."
                                        ),
                                        "maxLength": 240,
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }
