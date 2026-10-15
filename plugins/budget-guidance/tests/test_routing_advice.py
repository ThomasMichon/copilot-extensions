"""Budget-owner validity and real standalone consumer interoperability."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys

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
    advice = posture["routing_advice"]
    assert advice["availability"] == "available"
    assert advice["evaluated_at"] == posture["evaluated_at"]
    assert advice["valid_until"] == "2026-09-05T19:05:00Z"
    assert advice["warning_band"] == posture["calculated"]["warning_band"]


def test_owner_uses_earliest_field_freshness_deadline() -> None:
    advice = _posture(capture="2026-09-05T17:00:03Z")["routing_advice"]
    assert advice["valid_until"] == "2026-09-05T19:00:03Z"


def test_owner_bounds_advice_by_reset_and_rejects_future_captures() -> None:
    config = _config(_adapter(
        "manual", 1, _reading(
            "manual", "2026-09-05T19:00:00Z", allowance=Decimal(100),
            consumption=Decimal(20), reset_at="2026-09-05T19:00:01Z",
        ),
    ))
    assert build_posture(config, NOW)["routing_advice"]["valid_until"] == (
        "2026-09-05T19:00:01Z"
    )
    assert build_posture(config, NOW + timedelta(seconds=1))["routing_advice"][
        "availability"
    ] == "stale"
    future = _posture(capture="2026-09-05T19:00:01Z")["routing_advice"]
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
    advice = posture["routing_advice"]
    assert advice["availability"] != "available"
    assert advice["valid_until"] is None
    assert advice["warning_band"] is None


def test_real_owner_posture_is_consumed_without_budget_runtime_import(tmp_path: Path) -> None:
    posture = tmp_path / "posture.json"
    posture.write_text(json.dumps(_posture()), encoding="utf-8")
    home = tmp_path / "home"
    (home / ".copilot").mkdir(parents=True)
    (home / ".copilot" / "model-routing.json").write_text(json.dumps({
        "schema": "copilot-extensions.model-routing", "version": 1,
        "purposes": {"coding": [{
            "model": "qualified", "state": "demonstrated", "surfaces": ["task"],
            "evidence": [{
                "ref": "https://example.com/evidence", "observedAt": "2026-01-01",
                "sampleCount": 2,
            }],
        }]},
    }), encoding="utf-8")
    resolver = Path(__file__).resolve().parents[3] / (
        "plugins/agent-conduct-guidance/scripts/resolve-model-routing.py"
    )
    environment = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    environment.pop("PYTHONPATH", None)
    result = subprocess.run([
        sys.executable, "-E", "-s", str(resolver), "--repo", str(tmp_path),
        "--purpose", "coding", "--surface", "task", "--available-model", "qualified",
        "--as-of", "2026-09-05", "--budget-posture", str(posture),
        "--budget-at", "2026-09-05T19:00:00Z",
    ], capture_output=True, text=True, env=environment, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    decision = json.loads(result.stdout)["decision"]
    assert decision["model"] == "qualified"
    assert decision["budgetAdvice"]["availability"] == "available"
    assert decision["budgetAdvice"]["warningBand"] == "no-rate"
