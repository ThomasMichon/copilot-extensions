"""Build the one resolved posture consumed by every presentation surface."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from .math import calculate
from .models import BudgetConfig, TrailingRate, decimal_text, instant_text
from .resolve import FieldCandidate, Resolution, candidate_dict, resolve


def _value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_text(value)
    if isinstance(value, datetime):
        return instant_text(value)
    if isinstance(value, tuple) and all(isinstance(item, TrailingRate) for item in value):
        return [
            {
                "window_days": decimal_text(item.window_days),
                "rate_per_day": decimal_text(item.rate_per_day),
            }
            for item in value
        ]
    raise TypeError(f"unsupported posture value: {type(value).__name__}")


def _candidate(candidate: FieldCandidate) -> dict[str, Any]:
    return candidate_dict(candidate, _value(candidate.value))


def _render_fields(resolution: Resolution) -> dict[str, Any]:
    return {
        name: {
            "selected": _candidate(field.selected),
            "contradictions": [_candidate(item) for item in field.contradictions],
        }
        for name, field in resolution.fields.items()
    }


def _select_rate(resolution: Resolution) -> tuple[Decimal, Decimal] | None:
    field = resolution.fields.get("trailing_rates")
    if field is None or field.selected.freshness != "fresh":
        return None
    rates = field.selected.value
    return min(
        ((rate.window_days, rate.rate_per_day) for rate in rates),
        key=lambda item: item[0],
    )


def build_posture(config: BudgetConfig, at: datetime) -> dict[str, Any]:
    """Resolve configured readings and calculate a provider-neutral posture."""
    resolution = resolve(config, at)
    required_names = ("allowance", "consumption", "reset_at")
    missing = [name for name in required_names if name not in resolution.fields]
    failed = [
        adapter
        for adapter in resolution.adapters
        if adapter["availability"] in {"unavailable", "error"}
    ]
    posture: dict[str, Any] = {
        "schema": "copilot-extensions.budget-posture",
        "version": 1,
        "evaluated_at": instant_text(at),
        "availability": "available",
        "error": None,
        "missing_fields": missing,
        "adapters": list(resolution.adapters),
        "fields": _render_fields(resolution),
        "calculated": None,
    }
    if missing:
        posture["availability"] = "error" if failed and not resolution.fields else "unavailable"
        posture["error"] = (
            "required budget fields are unavailable: " + ", ".join(missing)
        )
        return posture

    required = [resolution.fields[name] for name in required_names]
    optional = [
        resolution.fields[name]
        for name in ("daily_ceiling", "trailing_rates")
        if name in resolution.fields
    ]
    calculated_fields = required + optional
    reset_elapsed = at > resolution.fields["reset_at"].selected.value
    stale = (
        reset_elapsed
        or any(field.selected.freshness == "stale" for field in calculated_fields)
    )
    contradictory = any(field.contradictions for field in calculated_fields)
    if stale:
        posture["availability"] = "stale"
        posture["error"] = (
            "budget period reset has elapsed; a rollover reading is required"
            if reset_elapsed
            else "one or more calculated fields are stale"
        )
    elif contradictory:
        posture["availability"] = "contradictory"
        posture["error"] = "one or more required fields have contradictory readings"

    ceiling_field = resolution.fields.get("daily_ceiling")
    result = calculate(
        allowance=resolution.fields["allowance"].selected.value,
        consumption=resolution.fields["consumption"].selected.value,
        reset_at=resolution.fields["reset_at"].selected.value,
        evaluated_at=at,
        daily_ceiling=(
            ceiling_field.selected.value
            if ceiling_field and ceiling_field.selected.freshness == "fresh"
            else None
        ),
        trailing_rate=_select_rate(resolution),
        stale=stale,
        contradictory=contradictory,
    )
    posture["calculated"] = {
        "evaluated_at": instant_text(result.evaluated_at),
        "remaining": decimal_text(result.remaining),
        "overspend": decimal_text(result.overspend),
        "seconds_remaining": decimal_text(result.seconds_remaining),
        "days_remaining": decimal_text(result.days_remaining),
        "sustainable_daily_rate": decimal_text(result.sustainable_daily_rate),
        "effective_daily_limit": decimal_text(result.effective_daily_limit),
        "projection_rate": decimal_text(result.projection_rate),
        "projection_window_days": decimal_text(result.projection_window_days),
        "projected_consumption": decimal_text(result.projected_consumption),
        "projected_remaining": decimal_text(result.projected_remaining),
        "projected_overspend": decimal_text(result.projected_overspend),
        "warning_band": result.warning_band,
    }
    return posture


def unavailable_posture(at: datetime, error: str) -> dict[str, Any]:
    """Build an explicit unavailable result without optimistic defaults."""
    return {
        "schema": "copilot-extensions.budget-posture",
        "version": 1,
        "evaluated_at": instant_text(at),
        "availability": "unavailable",
        "error": error,
        "missing_fields": ["allowance", "consumption", "reset_at"],
        "adapters": [],
        "fields": {},
        "calculated": None,
    }
