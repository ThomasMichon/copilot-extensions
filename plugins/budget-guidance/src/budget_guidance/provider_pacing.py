"""Same-period provider rate estimates and read-only pacing advice."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from .models import MAX_BUDGET_VALUE, ModelError, TrailingRate, decimal_text, instant_text, parse_instant
from .quota_values import quantity


def estimate_provider_rate(
    state: Mapping[str, Any], result: dict[str, Any], *,
    consumed: Decimal, reset: datetime, observed: datetime, at: datetime,
    max_age_seconds: int,
) -> TrailingRate | None:
    """Estimate cycle-average pace only from compatible provider monthly usage."""
    evidence: dict[str, Any] = {
        "availability": "unavailable",
        "kind": None,
        "cycle_start_inferred": False,
        "settlement_watermark": False,
    }
    result["rate_evidence"] = evidence

    def unavailable(diagnostic: str) -> None:
        evidence["diagnostic"] = diagnostic
        result["diagnostics"].append(diagnostic)

    monthly = state.get("monthlyUsage")
    if monthly is None:
        unavailable("provider-monthly-usage-unavailable")
        return None
    if not isinstance(monthly, Mapping):
        raise ModelError("provider monthlyUsage must be an object")
    for field, choices in (
        ("state", {"available", "no_policy", "unavailable", "unknown"}),
        ("scope", {"user", "unknown"}),
        ("unit", {"ai_credits", "requests", "tokens", "unknown"}),
    ):
        value = monthly.get(field)
        if value is None:
            evidence[field] = "unknown"
            continue
        if not isinstance(value, str) or value not in choices:
            raise ModelError(f"provider monthlyUsage.{field} is unsupported")
        evidence[field] = value
    if evidence["state"] != "available":
        unavailable("provider-monthly-usage-not-available")
        return None
    if evidence["scope"] != "user":
        unavailable("provider-monthly-usage-account-scope-unknown")
        return None
    if evidence["unit"] != "ai_credits":
        unavailable("provider-monthly-usage-unit-mismatch")
        return None
    if any(monthly.get(field) is None for field in (
        "consumedQuantity", "cycleStart", "queriedAt", "resetOn",
    )):
        unavailable("provider-monthly-usage-period-evidence-missing")
        return None
    usage = quantity(monthly["consumedQuantity"], "monthlyUsage.consumedQuantity")
    start = parse_instant(monthly["cycleStart"], "monthlyUsage.cycleStart")
    queried = parse_instant(monthly["queriedAt"], "monthlyUsage.queriedAt")
    period_reset = parse_instant(monthly["resetOn"], "monthlyUsage.resetOn")
    evidence.update(
        consumed_quantity=decimal_text(usage),
        cycle_start=instant_text(start), queried_at=instant_text(queried),
        reset_at=instant_text(period_reset),
    )
    if usage != consumed or period_reset != reset:
        unavailable("provider-monthly-usage-budget-conflict")
        return None
    if start >= queried or queried >= reset:
        unavailable("provider-monthly-usage-period-order-invalid")
        return None
    if queried > observed or queried > at:
        unavailable("provider-monthly-usage-after-account-observation")
        return None
    if at - queried > timedelta(seconds=max_age_seconds):
        unavailable("provider-monthly-usage-stale")
        return None
    window_days = Decimal((queried - start) // timedelta(microseconds=1)) / Decimal(86_400_000_000)
    rate = usage / window_days
    if rate > MAX_BUDGET_VALUE:
        unavailable("provider-cycle-average-exceeds-numeric-domain")
        return None
    evidence.update(
        availability="available", kind="cycle-average-estimate",
        window_days=decimal_text(window_days), rate_per_day=decimal_text(rate),
    )
    result["diagnostics"].append("cycle-average-is-not-a-measured-trailing-rate")
    return TrailingRate(window_days=window_days, rate_per_day=rate)


def apply_pacing_advice(result: dict[str, Any]) -> None:
    """Describe autonomous pacing without selecting models or changing workers."""
    calculated = result["calculated"]
    advice = result["advice"]
    advice["autonomous_recommendation"] = "unavailable"
    advice["projection_kind"] = None
    advice["overrun_warning"] = None
    if calculated["projection_rate"] is None:
        advice["reason"] = "no attributable same-period rate evidence"
        if calculated["warning_band"] == "overspent":
            advice["autonomous_recommendation"] = "reduce-aggressiveness"
            advice["overrun_warning"] = "observed-overspend"
            advice["reason"] = "observed overspend; review autonomous parallelism without blocking operator work"
        return
    advice["projection_kind"] = "cycle-average-estimate"
    if calculated["warning_band"] in {"critical", "overspent", "warning"}:
        advice["autonomous_recommendation"] = "reduce-aggressiveness"
        advice["overrun_warning"] = (
            "observed-overspend" if calculated["warning_band"] == "overspent"
            else "estimated-overrun"
        )
        advice["reason"] = (
            "review autonomous parallelism and lower avoidable aggressiveness; "
            "the estimate does not block operator work or authorize worker changes"
        )
    else:
        advice["autonomous_recommendation"] = "maintain-with-caution"
        advice["overrun_warning"] = "none-estimated"
        advice["reason"] = (
            "cycle-average estimate is within remaining pace; "
            "it does not establish per-worker cost or justify increased concurrency"
        )
