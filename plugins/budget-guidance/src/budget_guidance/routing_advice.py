"""Owner-issued, short-lived advice for optional routing consumers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .models import instant_text
from .resolve import Resolution

ADVICE_SCHEMA = "copilot-extensions.budget-routing-advice"
MAX_ADVICE_AGE_SECONDS = 300


def _deadline(at: datetime, seconds: int) -> datetime:
    ceiling = datetime.max.replace(tzinfo=timezone.utc)
    return at + min(timedelta(seconds=seconds), ceiling - at)


def routing_advice(
    posture: dict[str, Any], resolution: Resolution | None, at: datetime,
) -> dict[str, Any]:
    """Issue point-in-time advice without granting execution or pricing models."""
    availability = posture["availability"]
    reason = posture["error"]
    valid_until = None
    warning_band = None
    if availability == "available" and resolution is not None:
        selected = [field.selected for field in resolution.fields.values()]
        if any(field.captured_at > at for field in selected):
            availability = "unavailable"
            reason = "a calculated field was captured after evaluation"
        else:
            deadline = min(
                _deadline(at, MAX_ADVICE_AGE_SECONDS),
                resolution.fields["reset_at"].selected.value,
                *(
                    _deadline(field.captured_at, field.freshness_seconds)
                    for field in selected
                ),
            )
            if deadline <= at:
                availability = "stale"
                reason = "budget advice requires a newer reading"
            else:
                valid_until = instant_text(deadline)
                warning_band = posture["calculated"]["warning_band"]
    return {
        "schema": ADVICE_SCHEMA,
        "version": 1,
        "availability": availability,
        "evaluated_at": instant_text(at),
        "valid_until": valid_until,
        "warning_band": warning_band,
        "reason": reason,
    }
