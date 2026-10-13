"""Bounded SDK quantity and epoch parsing shared by quota projections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .models import MAX_BUDGET_VALUE, ModelError


def quantity(value: Any, field: str, *, signed: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ModelError(f"quota.{field} must be a finite number")
    number = Decimal(str(value))
    minimum = -MAX_BUDGET_VALUE if signed else Decimal(0)
    if not number.is_finite() or number < minimum or number > MAX_BUDGET_VALUE:
        raise ModelError(f"quota.{field} is outside the supported quantity range")
    return number


def epoch_instant(value: Any, field: str) -> datetime:
    milliseconds = quantity(value, field)
    if milliseconds != milliseconds.to_integral_value():
        raise ModelError(f"quota.{field} must be an integer")
    try:
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            milliseconds=int(milliseconds)
        )
    except OverflowError as exc:
        raise ModelError(f"quota.{field} is out of range") from exc
