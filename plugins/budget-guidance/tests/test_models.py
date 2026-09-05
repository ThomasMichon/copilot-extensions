from __future__ import annotations

from decimal import Decimal
import time

import pytest

from budget_guidance.models import ModelError, parse_config


def _reading(**overrides):
    value = {
        "schema": "copilot-extensions.budget-reading",
        "version": Decimal(1),
        "source": "manual-primary",
        "captured_at": "2026-09-05T12:00:00-07:00",
        "freshness_seconds": Decimal(3600),
        "availability": "available",
        "allowance": Decimal("100"),
        "consumption": Decimal("40"),
        "reset_at": "2026-09-10T12:00:00-07:00",
    }
    value.update(overrides)
    return value


def _config(reading=None):
    return {
        "schema": "copilot-extensions.budget-guidance-config",
        "version": Decimal(1),
        "adapters": [
            {
                "type": "static",
                "id": "primary",
                "authority": Decimal(10),
                "reading": reading or _reading(),
            }
        ],
    }


def test_rejects_unknown_config_and_schema_versions():
    value = _config()
    value["extra"] = True
    with pytest.raises(ModelError, match="unknown keys"):
        parse_config(value)

    value = _config()
    value["version"] = Decimal(2)
    with pytest.raises(ModelError, match="supported version 1"):
        parse_config(value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allowance", Decimal("-1")),
        ("consumption", Decimal("-1")),
        ("freshness_seconds", Decimal("1.5")),
        ("daily_ceiling", Decimal("0")),
    ],
)
def test_rejects_invalid_values(field, value):
    with pytest.raises(ModelError):
        parse_config(_config(_reading(**{field: value})))


def test_error_reading_requires_error_and_cannot_carry_values():
    with pytest.raises(ModelError, match="error is required"):
        parse_config(
            _config(
                _reading(
                    availability="error",
                    allowance=None,
                    consumption=None,
                    reset_at=None,
                )
            )
        )

    reading = _reading(availability="error", error="reader failed")
    with pytest.raises(ModelError, match="cannot carry budget values"):
        parse_config(_config(reading))


def test_rejects_executable_or_unsupported_adapter_shapes():
    value = _config()
    value["adapters"][0]["type"] = "command"
    value["adapters"][0]["command"] = ["echo", "ignored"]
    with pytest.raises(ModelError):
        parse_config(value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allowance", Decimal("1e999999999")),
        (
            "trailing_rates",
            [
                {
                    "window_days": Decimal(7),
                    "rate_per_day": Decimal("1e999999999"),
                }
            ],
        ),
    ],
)
def test_rejects_extreme_budget_values_before_arithmetic(field, value):
    with pytest.raises(ModelError, match="must not exceed"):
        parse_config(_config(_reading(**{field: value})))


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("freshness_seconds", Decimal("1e10000000")),
        ("authority", Decimal("1e10000000")),
    ],
)
def test_rejects_oversized_integers_before_conversion(target, value):
    config = _config()
    if target == "authority":
        config["adapters"][0]["authority"] = value
    else:
        config["adapters"][0]["reading"][target] = value

    started = time.monotonic()
    with pytest.raises(ModelError, match="must not exceed"):
        parse_config(config)
    assert time.monotonic() - started < 1


@pytest.mark.parametrize(
    "value",
    [
        "9999-12-31T23:59:59-01:00",
        "0001-01-01T00:00:00+01:00",
    ],
)
def test_rejects_timestamp_offsets_that_overflow_utc_normalization(value):
    with pytest.raises(ModelError, match="representable UTC instant"):
        parse_config(_config(_reading(captured_at=value)))
