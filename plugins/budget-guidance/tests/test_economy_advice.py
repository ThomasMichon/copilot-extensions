"""Current monthly economy advice without execution or model routing."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import json

import pytest

from budget_guidance.posture import build_posture, unavailable_posture
from test_posture import NOW, _adapter, _config, _reading


def _posture(*, capture: str = "2026-09-05T19:00:00Z", **fields: object) -> dict:
    return build_posture(_config(_adapter(
        "manual", 1, _reading(
            "manual", capture, allowance=Decimal(100), consumption=Decimal(20),
            reset_at="2026-09-06T19:00:00Z", **fields,
        ),
    )), NOW)


def test_owner_caps_snapshot_advice_age_without_recalculating_in_consumer() -> None:
    posture = _posture()
    advice = posture["economy_advice"]
    assert advice["availability"] == "available"
    assert advice["evaluated_at"] == posture["evaluated_at"]
    assert advice["valid_until"] == "2026-09-05T19:05:00Z"
    assert advice["warning_band"] == posture["calculated"]["warning_band"]


def test_owner_uses_earliest_field_freshness_deadline() -> None:
    advice = _posture(capture="2026-09-05T17:00:03Z")["economy_advice"]
    assert advice["valid_until"] == "2026-09-05T19:00:03Z"


def test_owner_bounds_advice_by_reset_and_rejects_future_captures() -> None:
    config = _config(_adapter(
        "manual", 1, _reading(
            "manual", "2026-09-05T19:00:00Z", allowance=Decimal(100),
            consumption=Decimal(20), reset_at="2026-09-05T19:00:01Z",
        ),
    ))
    assert build_posture(config, NOW)["economy_advice"]["valid_until"] == (
        "2026-09-05T19:00:01Z"
    )
    assert build_posture(config, NOW + timedelta(seconds=1))["economy_advice"][
        "availability"
    ] == "stale"
    future = _posture(capture="2026-09-05T19:00:01Z")["economy_advice"]
    assert future["availability"] == "unavailable"
    assert future["warning_band"] is None
    assert future["valid_until"] is None


@pytest.mark.parametrize("kind", ["stale", "missing", "contradictory", "unavailable"])
def test_owner_withholds_actionable_advice_for_failure_states(kind: str) -> None:
    if kind == "stale":
        posture = _posture(capture="2026-09-05T16:00:00Z")
    elif kind == "unavailable":
        posture = unavailable_posture(NOW, "not configured")
    else:
        config = _config(
            _adapter("a", 1, _reading(
                "a", "2026-09-05T19:00:00Z", allowance=Decimal(100),
                **({"consumption": Decimal(20), "reset_at": "2026-09-06T19:00:00Z"}
                   if kind == "contradictory" else {}),
            )),
            _adapter("b", 2, _reading(
                "b", "2026-09-05T19:00:00Z", allowance=Decimal(200),
            )),
        )
        posture = build_posture(config, NOW)
    advice = posture["economy_advice"]
    assert advice["availability"] != "available"
    assert advice["valid_until"] is None
    assert advice["warning_band"] is None
    assert advice["suggestions"] == []


def test_monthly_pressure_offers_only_optional_economy_suggestions() -> None:
    config = _config(_adapter("manual", 1, _reading(
        "manual", "2026-09-05T19:00:00Z", allowance=Decimal(100),
        consumption=Decimal(120), reset_at="2026-09-06T19:00:00Z",
    )))
    posture = build_posture(config, NOW)
    assert posture["calculated"]["overspend"] == "20"
    assert "routing_advice" not in posture
    advice = posture["economy_advice"]
    assert advice["schema"] == "copilot-extensions.budget-economy-advice"
    assert advice["warning_band"] == "overspent"
    assert advice["suggestions"] and all(
        suggestion.startswith("Consider ") for suggestion in advice["suggestions"]
    )
    assert not {"model", "concurrency", "launchBlocked", "action"} & advice.keys()
    assert "model" not in json.dumps(advice)
