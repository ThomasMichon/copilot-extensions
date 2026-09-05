"""Pure deterministic budget calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

SECONDS_PER_DAY = Decimal(86400)


@dataclass(frozen=True)
class BudgetMath:
    """Calculated posture values."""

    evaluated_at: datetime
    remaining: Decimal
    overspend: Decimal
    seconds_remaining: Decimal
    days_remaining: Decimal
    sustainable_daily_rate: Decimal
    effective_daily_limit: Decimal
    projection_rate: Decimal | None
    projection_window_days: Decimal | None
    projected_consumption: Decimal | None
    projected_remaining: Decimal | None
    projected_overspend: Decimal | None
    warning_band: str


def calculate(
    *,
    allowance: Decimal,
    consumption: Decimal,
    reset_at: datetime,
    evaluated_at: datetime,
    daily_ceiling: Decimal | None,
    trailing_rate: tuple[Decimal, Decimal] | None,
    stale: bool,
    contradictory: bool,
) -> BudgetMath:
    """Calculate balance, sustainable pace, projection, and warning state."""
    balance = allowance - consumption
    remaining = max(balance, Decimal(0))
    overspend = max(-balance, Decimal(0))
    seconds_remaining = max(
        Decimal(str((reset_at - evaluated_at).total_seconds())),
        Decimal(0),
    )
    days_remaining = seconds_remaining / SECONDS_PER_DAY
    sustainable = remaining / days_remaining if days_remaining > 0 else Decimal(0)
    effective = min(sustainable, daily_ceiling) if daily_ceiling is not None else sustainable

    projection_rate: Decimal | None = None
    projection_window: Decimal | None = None
    projected_consumption: Decimal | None = None
    projected_remaining: Decimal | None = None
    projected_overspend: Decimal | None = None
    if trailing_rate is not None:
        projection_window, projection_rate = trailing_rate
        projected_consumption = consumption + projection_rate * days_remaining
        projected_balance = allowance - projected_consumption
        projected_remaining = max(projected_balance, Decimal(0))
        projected_overspend = max(-projected_balance, Decimal(0))

    if stale:
        warning = "stale"
    elif contradictory:
        warning = "contradictory"
    elif overspend > 0:
        warning = "overspent"
    elif seconds_remaining == 0:
        warning = "reset-due"
    elif trailing_rate is None:
        warning = "no-rate"
    elif projected_overspend is not None and projected_overspend > 0:
        warning = "critical"
    elif projection_rate is not None and projection_rate > effective:
        warning = "warning"
    else:
        warning = "on-track"

    return BudgetMath(
        evaluated_at=evaluated_at,
        remaining=remaining,
        overspend=overspend,
        seconds_remaining=seconds_remaining,
        days_remaining=days_remaining,
        sustainable_daily_rate=sustainable,
        effective_daily_limit=effective,
        projection_rate=projection_rate,
        projection_window_days=projection_window,
        projected_consumption=projected_consumption,
        projected_remaining=projected_remaining,
        projected_overspend=projected_overspend,
        warning_band=warning,
    )
