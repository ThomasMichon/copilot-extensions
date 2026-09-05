from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from budget_guidance.models import parse_config
from budget_guidance.posture import build_posture

NOW = datetime(2026, 9, 5, 19, 0, tzinfo=timezone.utc)


def _reading(source, captured, **fields):
    return {
        "schema": "copilot-extensions.budget-reading",
        "version": Decimal(1),
        "source": source,
        "captured_at": captured,
        "freshness_seconds": Decimal(7200),
        "availability": "available",
        **fields,
    }


def _config(*adapters):
    return parse_config(
        {
            "schema": "copilot-extensions.budget-guidance-config",
            "version": Decimal(1),
            "adapters": list(adapters),
        }
    )


def _adapter(adapter_id, authority, reading):
    return {
        "type": "static",
        "id": adapter_id,
        "authority": Decimal(authority),
        "reading": reading,
    }


def test_composes_partial_fields_and_preserves_higher_authority_contradiction():
    config = _config(
        _adapter(
            "entitlement",
            1,
            _reading(
                "contract",
                "2026-09-05T18:00:00Z",
                allowance=Decimal("100"),
                reset_at="2026-09-10T18:00:00Z",
            ),
        ),
        _adapter(
            "usage",
            2,
            _reading(
                "usage-report",
                "2026-09-05T19:00:00Z",
                consumption=Decimal("40"),
                allowance=Decimal("120"),
                trailing_rates=[
                    {
                        "window_days": Decimal(7),
                        "rate_per_day": Decimal(8),
                    }
                ],
            ),
        ),
    )
    posture = build_posture(config, NOW)

    assert posture["fields"]["allowance"]["selected"]["value"] == "100"
    assert posture["fields"]["allowance"]["selected"]["source"] == "contract"
    assert posture["fields"]["allowance"]["contradictions"][0]["value"] == "120"
    assert posture["fields"]["consumption"]["selected"]["source"] == "usage-report"
    assert posture["availability"] == "contradictory"


def test_fresher_value_wins_within_equal_authority():
    config = _config(
        _adapter(
            "older",
            5,
            _reading("older", "2026-09-05T18:00:00Z", consumption=Decimal("20")),
        ),
        _adapter(
            "newer",
            5,
            _reading(
                "newer",
                "2026-09-05T19:00:00Z",
                allowance=Decimal("100"),
                consumption=Decimal("25"),
                reset_at="2026-09-06T19:00:00Z",
            ),
        ),
    )
    posture = build_posture(config, NOW)
    assert posture["fields"]["consumption"]["selected"]["source"] == "newer"
    assert posture["fields"]["consumption"]["contradictions"][0]["source"] == "older"


def test_far_future_microseconds_preserve_newest_reading_precedence():
    config = _config(
        _adapter(
            "older",
            5,
            _reading(
                "older",
                "9999-12-30T23:59:59.000001Z",
                consumption=Decimal("20"),
            ),
        ),
        _adapter(
            "newer",
            5,
            _reading(
                "newer",
                "9999-12-30T23:59:59.000002Z",
                allowance=Decimal("100"),
                consumption=Decimal("25"),
                reset_at="9999-12-31T23:59:59Z",
            ),
        ),
    )

    posture = build_posture(
        config,
        datetime(9999, 12, 30, 23, 59, 59, 2, tzinfo=timezone.utc),
    )

    assert posture["fields"]["consumption"]["selected"]["source"] == "newer"
    assert posture["fields"]["consumption"]["contradictions"][0]["source"] == "older"


def test_partial_day_math_ceiling_and_projection():
    config = _config(
        _adapter(
            "manual",
            1,
            _reading(
                "manual",
                "2026-09-05T19:00:00Z",
                allowance=Decimal("100"),
                consumption=Decimal("40"),
                reset_at="2026-09-07T07:00:00Z",
                daily_ceiling=Decimal("30"),
                trailing_rates=[
                    {"window_days": Decimal(7), "rate_per_day": Decimal("50")}
                ],
            ),
        )
    )
    calculated = build_posture(config, NOW)["calculated"]
    assert calculated["remaining"] == "60"
    assert calculated["overspend"] == "0"
    assert calculated["days_remaining"] == "1.5"
    assert calculated["sustainable_daily_rate"] == "40"
    assert calculated["effective_daily_limit"] == "30"
    assert calculated["projected_consumption"] == "115"
    assert calculated["projected_overspend"] == "15"
    assert calculated["warning_band"] == "critical"


def test_projection_uses_shortest_fresh_supported_trailing_window():
    config = _config(
        _adapter(
            "manual",
            1,
            _reading(
                "manual",
                "2026-09-05T19:00:00Z",
                allowance=Decimal("100"),
                consumption=Decimal("20"),
                reset_at="2026-09-06T19:00:00Z",
                trailing_rates=[
                    {"window_days": Decimal(30), "rate_per_day": Decimal("5")},
                    {"window_days": Decimal(7), "rate_per_day": Decimal("8")},
                ],
            ),
        )
    )
    calculated = build_posture(config, NOW)["calculated"]
    assert calculated["projection_window_days"] == "7"
    assert calculated["projection_rate"] == "8"
    assert calculated["projected_consumption"] == "28"


def test_completed_budget_period_is_stale_and_horizon_uses_evaluation_time():
    config = _config(
        _adapter(
            "manual",
            1,
            {
                **_reading(
                    "manual",
                    "2026-09-01T00:00:00Z",
                    allowance=Decimal("100"),
                    consumption=Decimal("25"),
                    reset_at="2026-09-02T00:00:00Z",
                ),
                "freshness_seconds": Decimal(604800),
            },
        )
    )

    posture = build_posture(
        config,
        datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc),
    )

    assert posture["availability"] == "stale"
    assert "rollover reading is required" in posture["error"]
    assert posture["calculated"]["evaluated_at"] == "2026-09-03T00:00:00Z"
    assert posture["calculated"]["seconds_remaining"] == "0"
    assert posture["calculated"]["warning_band"] == "stale"


def test_reset_boundary_overspend_stale_and_no_rate_states():
    boundary = _config(
        _adapter(
            "manual",
            1,
            _reading(
                "manual",
                "2026-09-05T19:00:00Z",
                allowance=Decimal("10"),
                consumption=Decimal("12"),
                reset_at="2026-09-05T19:00:00Z",
            ),
        )
    )
    calculated = build_posture(boundary, NOW)["calculated"]
    assert calculated["seconds_remaining"] == "0"
    assert calculated["overspend"] == "2"
    assert calculated["sustainable_daily_rate"] == "0"
    assert calculated["warning_band"] == "overspent"

    stale = _config(
        _adapter(
            "manual",
            1,
            {
                **_reading(
                    "manual",
                    "2026-09-01T00:00:00Z",
                    allowance=Decimal("10"),
                    consumption=Decimal("1"),
                    reset_at="2026-09-10T00:00:00Z",
                ),
                "freshness_seconds": Decimal(60),
            },
        )
    )
    stale_posture = build_posture(stale, NOW)
    assert stale_posture["availability"] == "stale"
    assert stale_posture["calculated"]["warning_band"] == "stale"

    no_rate = _config(
        _adapter(
            "manual",
            1,
            _reading(
                "manual",
                "2026-09-05T19:00:00Z",
                allowance=Decimal("10"),
                consumption=Decimal("1"),
                reset_at="2026-09-06T19:00:00Z",
            ),
        )
    )
    assert build_posture(no_rate, NOW)["calculated"]["warning_band"] == "no-rate"


def test_failed_or_missing_data_never_becomes_optimistic_defaults():
    config = _config(
        _adapter(
            "failed",
            1,
            {
                "schema": "copilot-extensions.budget-reading",
                "version": Decimal(1),
                "source": "manual",
                "captured_at": "2026-09-05T19:00:00Z",
                "freshness_seconds": Decimal(60),
                "availability": "error",
                "error": "unavailable",
            },
        )
    )
    posture = build_posture(config, NOW)
    assert posture["availability"] == "error"
    assert posture["calculated"] is None
    assert posture["fields"] == {}
