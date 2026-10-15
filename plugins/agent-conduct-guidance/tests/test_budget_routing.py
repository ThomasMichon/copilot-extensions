"""Optional posture advice must never change the qualification boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_model_routing import _config, _repo, _run, _write

AT = "2030-01-10T12:00:00Z"
UNTIL = "2030-01-10T12:05:00Z"


def _posture(**updates: object) -> dict[str, object]:
    return {
        "schema": "copilot-extensions.budget-posture",
        "version": 1,
        "routing_advice": {
            "schema": "copilot-extensions.budget-routing-advice",
            "version": 1,
            "availability": "available",
            "evaluated_at": AT,
            "valid_until": UNTIL,
            "warning_band": "on-track",
            "reason": None,
            **updates,
        },
    }


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, list[str]]:
    repo, home = _repo(tmp_path)
    entries = []
    for model, state, rank in (
        ("cheap", "demonstrated", 2),
        ("preferred", "demonstrated", 4),
        ("trial", "candidate", 0),
        ("hold", "held", 0),
        ("failed", "failed", 0),
    ):
        entry = {
            "model": model, "state": state, "costRank": rank, "surfaces": ["task"],
        }
        if state == "demonstrated":
            entry["evidence"] = [{
                "ref": "https://example.com/result", "observedAt": "2030-01-01",
                "sampleCount": 3,
            }]
        entries.append(entry)
    _write(home / ".copilot" / "model-routing.json", _config({"coding": entries}))
    posture = tmp_path / "posture.json"
    _write(posture, _posture())
    args = ["--purpose", "coding", "--surface", "task", "--as-of", "2030-01-10"]
    for model in ("cheap", "preferred", "trial", "hold", "failed"):
        args.extend(["--available-model", model])
    return repo, home, posture, args


def _decision(repo: Path, home: Path, posture: Path, args: list[str]) -> dict:
    return json.loads(_run(
        repo, home, *args, "--budget-posture", str(posture), "--budget-at", AT,
    ).stdout)["decision"]


@pytest.mark.parametrize(
    ("band", "action"),
    [
        ("on-track", "recommendation"), ("no-rate", "caution"), ("warning", "caution"),
        ("critical", "escalation"), ("overspent", "escalation"), ("reset-due", "escalation"),
    ],
)
def test_budget_advice_does_not_change_baseline_selection(
    tmp_path: Path, band: str, action: str,
) -> None:
    repo, home, posture, args = _setup(tmp_path)
    baseline = json.loads(_run(repo, home, *args).stdout)["decision"]
    assert "budgetAdvice" not in baseline
    _write(posture, _posture(warning_band=band))
    composed = _decision(repo, home, posture, args)
    advice = composed.pop("budgetAdvice")
    assert composed == baseline
    assert advice["action"] == action
    assert advice["recommendedModel"] == "cheap"
    assert advice["fit"] == "unknown"
    assert advice["launchBlocked"] is False


@pytest.mark.parametrize("availability", ["stale", "contradictory", "unavailable", "error"])
def test_budget_failure_states_do_not_offer_models(
    tmp_path: Path, availability: str,
) -> None:
    repo, home, posture, args = _setup(tmp_path)
    _write(posture, _posture(
        availability=availability, warning_band=None, valid_until=None,
    ))
    decision = _decision(repo, home, posture, args)
    assert decision["model"] == "cheap"
    assert decision["budgetAdvice"]["availability"] == availability
    assert decision["budgetAdvice"]["action"] == "caution"
    assert "recommendedModel" not in decision["budgetAdvice"]


@pytest.mark.parametrize(
    "updates",
    [
        {"version": True}, {"availability": []}, {"warning_band": "invented"},
        {"valid_until": "2030-01-10T12:05:00.000001Z"},
        {"valid_until": AT}, {"evaluated_at": "2030-01-10T12:01:00Z"},
        {"evaluated_at": "2030-01-10T12:00:00"}, {"unknown": 0},
        {"availability": "stale"}, {"reason": 0},
    ],
)
def test_malformed_budget_is_visible_and_does_not_change_route(
    tmp_path: Path, updates: dict[str, object],
) -> None:
    repo, home, posture, args = _setup(tmp_path)
    _write(posture, _posture(**updates))
    result = _run(
        repo, home, *args, "--budget-posture", str(posture), "--budget-at", AT,
    )
    decision = json.loads(result.stdout)["decision"]
    assert decision["model"] == "cheap"
    assert decision["budgetAdvice"]["availability"] == "unavailable"
    assert "[agent-conduct-guidance]" in result.stderr


@pytest.mark.parametrize(
    ("at", "availability"),
    [("2030-01-10T12:04:59.999999Z", "available"), (UNTIL, "stale")],
)
def test_budget_advice_expires_at_owner_deadline(
    tmp_path: Path, at: str, availability: str,
) -> None:
    repo, home, posture, args = _setup(tmp_path)
    result = _run(
        repo, home, *args, "--budget-posture", str(posture), "--budget-at", at,
    )
    advice = json.loads(result.stdout)["decision"]["budgetAdvice"]
    assert advice["availability"] == availability
    assert ("warningBand" in advice) == (availability == "available")


@pytest.mark.parametrize("mode", ["operator", "trial"])
def test_explicit_operator_choice_and_trial_are_preserved(
    tmp_path: Path, mode: str,
) -> None:
    repo, home, posture, args = _setup(tmp_path)
    _write(posture, _posture(warning_band="critical"))
    args += (
        ["--operator-model", "preferred"] if mode == "operator"
        else ["--trial-model", "trial", "--trial-id", "controlled-trial"]
    )
    decision = _decision(repo, home, posture, args)
    assert decision["mode"] == mode
    assert decision["model"] == ("preferred" if mode == "operator" else "trial")
    assert "recommendedModel" not in decision["budgetAdvice"]
    assert decision["budgetAdvice"]["launchBlocked"] is False


@pytest.mark.parametrize("model", ["hold", "failed", "trial", "absent"])
def test_operator_flag_does_not_qualify_ineligible_models(
    tmp_path: Path, model: str,
) -> None:
    repo, home, posture, args = _setup(tmp_path)
    decision = _decision(repo, home, posture, args + ["--operator-model", model])
    assert decision["status"] == "no-eligible-model"
    assert decision["budgetAdvice"]["action"] == "no-fit"
    assert "model" not in decision
    assert "recommendedModel" not in decision["budgetAdvice"]


def test_no_fit_and_budget_pressure_never_promote_a_candidate(tmp_path: Path) -> None:
    repo, home, posture, args = _setup(tmp_path)
    _write(posture, _posture(warning_band="critical"))
    decision = _decision(repo, home, posture, args + ["--surface", "unsupported"])
    assert decision["status"] == "no-eligible-model"
    assert decision["budgetAdvice"]["action"] == "no-fit"
    assert "model" not in decision


@pytest.mark.parametrize("kind", ["missing", "oversized", "duplicate", "legacy", "directory"])
def test_invalid_budget_files_are_explicit_cautions(tmp_path: Path, kind: str) -> None:
    repo, home, posture, args = _setup(tmp_path)
    if kind == "missing":
        posture = tmp_path / "missing.json"
    elif kind == "directory":
        posture = tmp_path
    elif kind == "oversized":
        posture.write_bytes(b" " * 65537)
    elif kind == "duplicate":
        posture.write_text('{"schema":1,"schema":2}', encoding="utf-8")
    else:
        _write(posture, {"schema": "copilot-extensions.budget-posture", "version": 1})
    advice = _decision(repo, home, posture, args)["budgetAdvice"]
    assert advice["availability"] == "unavailable"
    assert advice["action"] == "caution"


def test_budget_arguments_and_explicit_choice_are_validated(tmp_path: Path) -> None:
    repo, home, posture, args = _setup(tmp_path)
    for extra in (
        ["--budget-at", AT],
        ["--operator-model", "cheap", "--trial-model", "trial", "--trial-id", "x"],
        ["--budget-posture", str(posture), "--budget-at", "invalid"],
    ):
        assert _run(repo, home, *args, *extra, check=False).returncode != 0
