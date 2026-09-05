"""Strict versioned configuration and reading models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

CONFIG_SCHEMA = "copilot-extensions.budget-guidance-config"
READING_SCHEMA = "copilot-extensions.budget-reading"
SCHEMA_VERSION = 1
MAX_BUDGET_VALUE = Decimal("1e18")
MAX_FRESHNESS_SECONDS = 315_576_000
MAX_AUTHORITY = 1_000_000
READING_FIELDS = (
    "allowance",
    "consumption",
    "reset_at",
    "trailing_rates",
    "daily_ceiling",
)


class ModelError(ValueError):
    """Raised when declarative budget data violates its schema."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ModelError(f"duplicate key: {key}")
        value[key] = item
    return value


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object while preserving decimals and rejecting duplicates."""
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_float=Decimal,
            parse_int=Decimal,
            object_pairs_hook=_strict_object,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelError(f"cannot read JSON configuration: {exc}") from exc
    if not isinstance(value, dict):
        raise ModelError("configuration root must be an object")
    return value


def _exact_keys(
    value: dict[str, Any],
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise ModelError(f"{label} missing required keys: {', '.join(sorted(missing))}")
    if unknown:
        raise ModelError(f"{label} contains unknown keys: {', '.join(sorted(unknown))}")


def _versioned(value: dict[str, Any], schema: str, label: str) -> None:
    if value.get("schema") != schema:
        raise ModelError(f"{label}.schema must be {schema!r}")
    version = value.get("version")
    if not isinstance(version, Decimal) or version != SCHEMA_VERSION:
        raise ModelError(f"{label}.version must be the supported version {SCHEMA_VERSION}")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelError(f"{label} must be a non-empty string")
    return value.strip()


def _decimal(value: Any, label: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ModelError(f"{label} must be a finite JSON number")
    if positive and value <= 0:
        raise ModelError(f"{label} must be greater than zero")
    if not positive and value < 0:
        raise ModelError(f"{label} must be non-negative")
    if value > MAX_BUDGET_VALUE:
        raise ModelError(
            f"{label} must not exceed {MAX_BUDGET_VALUE:.0f}"
        )
    return value


def _integer(value: Any, label: str, *, maximum: int) -> int:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ModelError(f"{label} must be a non-negative finite JSON integer")
    if value.adjusted() > len(str(maximum)) - 1 or value > maximum:
        raise ModelError(f"{label} must not exceed {maximum}")
    number = value
    if number != number.to_integral_value():
        raise ModelError(f"{label} must be an integer")
    return int(number)


def parse_instant(value: Any, label: str) -> datetime:
    """Parse a timezone-qualified RFC 3339 instant and normalize it to UTC."""
    if not isinstance(value, str):
        raise ModelError(f"{label} must be an RFC 3339 string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        instant = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ModelError(f"{label} must be a valid RFC 3339 instant") from exc
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ModelError(f"{label} must include a timezone offset")
    try:
        return instant.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ModelError(
            f"{label} must normalize to a representable UTC instant"
        ) from exc


@dataclass(frozen=True)
class TrailingRate:
    """A measured average consumption rate over a trailing window."""

    window_days: Decimal
    rate_per_day: Decimal


@dataclass(frozen=True)
class BudgetReading:
    """One attributable partial or complete adapter reading."""

    source: str
    captured_at: datetime
    freshness_seconds: int
    availability: str
    error: str | None
    allowance: Decimal | None = None
    consumption: Decimal | None = None
    reset_at: datetime | None = None
    trailing_rates: tuple[TrailingRate, ...] | None = None
    daily_ceiling: Decimal | None = None


@dataclass(frozen=True)
class StaticAdapter:
    """An inert manual/static reading with explicit field authority."""

    adapter_id: str
    authority: int
    reading: BudgetReading


@dataclass(frozen=True)
class BudgetConfig:
    """Version 1 budget-guidance configuration."""

    adapters: tuple[StaticAdapter, ...]


def _parse_rate(value: Any, index: int, label: str) -> TrailingRate:
    rate_label = f"{label}.trailing_rates[{index}]"
    if not isinstance(value, dict):
        raise ModelError(f"{rate_label} must be an object")
    _exact_keys(value, {"window_days", "rate_per_day"}, set(), rate_label)
    return TrailingRate(
        window_days=_decimal(
            value["window_days"],
            f"{rate_label}.window_days",
            positive=True,
        ),
        rate_per_day=_decimal(value["rate_per_day"], f"{rate_label}.rate_per_day"),
    )


def parse_reading(value: Any, label: str = "reading") -> BudgetReading:
    """Parse one strict version 1 adapter reading."""
    if not isinstance(value, dict):
        raise ModelError(f"{label} must be an object")
    required = {
        "schema",
        "version",
        "source",
        "captured_at",
        "freshness_seconds",
        "availability",
    }
    optional = set(READING_FIELDS) | {"error"}
    _exact_keys(value, required, optional, label)
    _versioned(value, READING_SCHEMA, label)

    availability = value["availability"]
    if availability not in {"available", "unavailable", "error"}:
        raise ModelError(f"{label}.availability must be available, unavailable, or error")
    error = value.get("error")
    if error is not None:
        error = _text(error, f"{label}.error")
    if availability == "error" and error is None:
        raise ModelError(f"{label}.error is required when availability is error")

    supplied = {
        field
        for field in READING_FIELDS
        if field in value and value[field] is not None
    }
    if availability != "available" and supplied:
        raise ModelError(
            f"{label} cannot carry budget values when availability is {availability}"
        )

    rates: tuple[TrailingRate, ...] | None = None
    if "trailing_rates" in value and value["trailing_rates"] is not None:
        raw_rates = value["trailing_rates"]
        if not isinstance(raw_rates, list) or not raw_rates:
            raise ModelError(f"{label}.trailing_rates must be a non-empty array")
        rates = tuple(_parse_rate(rate, index, label) for index, rate in enumerate(raw_rates))
        windows = [rate.window_days for rate in rates]
        if len(windows) != len(set(windows)):
            raise ModelError(f"{label}.trailing_rates contains duplicate window_days")

    return BudgetReading(
        source=_text(value["source"], f"{label}.source"),
        captured_at=parse_instant(value["captured_at"], f"{label}.captured_at"),
        freshness_seconds=_integer(
            value["freshness_seconds"],
            f"{label}.freshness_seconds",
            maximum=MAX_FRESHNESS_SECONDS,
        ),
        availability=availability,
        error=error,
        allowance=_decimal(value["allowance"], f"{label}.allowance")
        if value.get("allowance") is not None
        else None,
        consumption=_decimal(value["consumption"], f"{label}.consumption")
        if value.get("consumption") is not None
        else None,
        reset_at=parse_instant(value["reset_at"], f"{label}.reset_at")
        if value.get("reset_at") is not None
        else None,
        trailing_rates=rates,
        daily_ceiling=_decimal(
            value["daily_ceiling"],
            f"{label}.daily_ceiling",
            positive=True,
        )
        if value.get("daily_ceiling") is not None
        else None,
    )


def parse_config(value: dict[str, Any]) -> BudgetConfig:
    """Parse strict version 1 configuration without executing any value."""
    _exact_keys(value, {"schema", "version", "adapters"}, set(), "config")
    _versioned(value, CONFIG_SCHEMA, "config")
    raw_adapters = value["adapters"]
    if not isinstance(raw_adapters, list):
        raise ModelError("config.adapters must be an array")
    adapters: list[StaticAdapter] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_adapters):
        label = f"config.adapters[{index}]"
        if not isinstance(raw, dict):
            raise ModelError(f"{label} must be an object")
        _exact_keys(raw, {"type", "id", "authority", "reading"}, set(), label)
        if raw["type"] != "static":
            raise ModelError(f"{label}.type is unsupported: {raw['type']!r}")
        adapter_id = _text(raw["id"], f"{label}.id")
        if adapter_id in seen_ids:
            raise ModelError(f"duplicate adapter id: {adapter_id}")
        seen_ids.add(adapter_id)
        adapters.append(
            StaticAdapter(
                adapter_id=adapter_id,
                authority=_integer(
                    raw["authority"],
                    f"{label}.authority",
                    maximum=MAX_AUTHORITY,
                ),
                reading=parse_reading(raw["reading"], f"{label}.reading"),
            )
        )
    return BudgetConfig(adapters=tuple(adapters))


def decimal_text(value: Decimal | None) -> str | None:
    """Return a stable non-exponent decimal representation."""
    if value is None:
        return None
    text = format(value.normalize(), "f")
    return "0" if text == "-0" else text


def instant_text(value: datetime | None) -> str | None:
    """Serialize an instant as UTC RFC 3339."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
