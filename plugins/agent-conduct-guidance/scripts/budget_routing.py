"""Optional inert budget-advice consumer; no budget runtime dependency."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
from typing import Any

MAX_POSTURE_BYTES = 65536
STATES = {"available", "unavailable", "error", "stale", "contradictory"}
BANDS = {"on-track", "no-rate", "warning", "critical", "overspent", "reset-due"}
KEYS = {
    "schema", "version", "availability", "evaluated_at",
    "valid_until", "warning_band", "reason",
}


class AdviceError(ValueError):
    """An optional budget snapshot is not usable."""


def instant(value: Any) -> datetime:
    """Require a timezone-qualified, representable advice instant."""
    if not isinstance(value, str):
        raise AdviceError("budget advice timestamp must be a string")
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("missing timezone")
        return at.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise AdviceError("budget advice timestamp must include a valid timezone") from exc


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdviceError("budget posture contains duplicate keys")
        result[key] = value
    return result


def _read(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise AdviceError("budget posture must be a regular file")
        data = stream.read(MAX_POSTURE_BYTES + 1)
    if len(data) > MAX_POSTURE_BYTES:
        raise AdviceError("budget posture exceeds 65536 bytes")
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_object)
    if (
        not isinstance(value, dict)
        or value.get("schema") != "copilot-extensions.budget-posture"
        or type(value.get("version")) is not int or value["version"] != 1
    ):
        raise AdviceError("unsupported budget posture schema/version")
    advice = value.get("routing_advice")
    if not isinstance(advice, dict) or set(advice) != KEYS:
        raise AdviceError("budget posture lacks supported owner-issued routing advice")
    if (
        advice["schema"] != "copilot-extensions.budget-routing-advice"
        or type(advice["version"]) is not int or advice["version"] != 1
        or not isinstance(advice["availability"], str)
        or advice["availability"] not in STATES
        or (advice["reason"] is not None and not isinstance(advice["reason"], str))
    ):
        raise AdviceError("unsupported budget routing advice contract")
    return advice


def compose(
    path: Path, decision: dict[str, Any], at: datetime,
) -> tuple[dict[str, Any], str | None]:
    """Append advice without changing routing admission, selection, or fallbacks."""
    result: dict[str, Any] = {
        "availability": "unavailable", "action": "caution",
        "fit": "unknown", "launchBlocked": False,
        "reason": "budget advice unavailable",
    }
    try:
        advice = _read(path)
        evaluated = instant(advice["evaluated_at"])
        if evaluated > at:
            raise AdviceError("budget advice evaluation is in the future")
        result["availability"] = advice["availability"]
        result["evaluatedAt"] = advice["evaluated_at"]
        if advice["availability"] != "available":
            if advice["valid_until"] is not None or advice["warning_band"] is not None:
                raise AdviceError("unavailable budget advice must withhold recommendations")
            result["reason"] = "budget posture is " + advice["availability"]
            return result, None
        deadline = instant(advice["valid_until"])
        band = advice["warning_band"]
        if (
            not isinstance(band, str) or band not in BANDS
            or deadline <= evaluated or deadline - evaluated > timedelta(seconds=300)
        ):
            raise AdviceError("budget advice has an invalid band or validity interval")
        if at >= deadline:
            result["availability"] = "stale"
            result["reason"] = "budget advice expired; obtain a fresh owner-issued posture"
            return result, None
        result["validUntil"] = advice["valid_until"]
        result["warningBand"] = band
        if decision["status"] != "selected":
            result["action"] = "no-fit"
            result["reason"] = "budget advice cannot supply an eligible demonstrated model"
        elif decision["mode"] in {"trial", "operator"}:
            result["reason"] = "explicit choice preserved; budget advice cannot override admission"
        else:
            result["action"] = "recommendation"
            result["recommendedModel"] = decision["model"]
            result["reason"] = (
                "retain lowest-cost demonstrated eligible choice; "
                "account posture does not establish per-task affordability"
            )
        if decision["status"] != "selected":
            return result, None
        if band in {"critical", "overspent", "reset-due"}:
            result["action"] = "escalation"
            result["reason"] += "; budget pressure requires operator attention, not a launch block"
        elif band in {"warning", "no-rate"}:
            result["action"] = "caution"
            result["reason"] += "; pacing is uncertain or exceeds the advised rate"
        return result, None
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        diagnostic = (
            str(exc) if isinstance(exc, AdviceError)
            else "budget posture is unreadable or invalid JSON"
        )
        result.update(
            availability="unavailable", action="caution",
            reason=diagnostic,
        )
        return result, diagnostic
