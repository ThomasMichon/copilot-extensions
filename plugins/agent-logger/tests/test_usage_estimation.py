"""Known synthetic relationships and honest session-held-out uncertainty."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_logger.__main__ import main
from agent_logger.usage_calibration import CalibrationPolicy, calibrate, is_validation
from agent_logger.usage_estimation import estimate
from agent_logger.usage_feature_contract import admit_sessions
from agent_logger.usage_features import FEATURE_METHOD, FEATURE_SCHEMA, _digest
from agent_logger.usage_ledger import UsageEvidenceError


def document(sid: str, *, measured: bool = True, scale: int = 1) -> dict:
    return {
        "schema": FEATURE_SCHEMA, "version": 1, "method": FEATURE_METHOD,
        "session_id": sid,
        "source_digest": hashlib.sha256(sid.encode()).hexdigest(),
        "calls": [{
            "turn": 1, "api_call_id": "a", "model": "model-a",
            "input_chars": 100 * scale, "output_chars": 10 * scale,
            "input_tokens": 50 * scale if measured else None,
            "output_tokens": 5 * scale if measured else None, "emissions": 1,
        }],
        "warnings": [],
    }


def cohort() -> list[dict]:
    return [document(f"synthetic-{i}", scale=i % 3 + 1) for i in range(50)]


@pytest.mark.guard
def test_usage_estimation_recovers_known_relationship_and_separates_exact() -> None:
    calibration = calibrate(cohort(), cohort="synthetic")
    metric = calibration["models"]["model-a"]["input"]
    assert metric["coefficient"] == .5
    assert metric["held_out_relative_error"]["max"] == 0
    assert calibration["validation_unit"] == "session-model matched-call aggregate"
    historical = document("historical", measured=False)
    row = estimate([historical], calibration)["sessions"][0]["models"][0]
    assert row["input"]["estimated_tokens"] == 50
    assert row["input"]["measured_tokens"] is None
    assert row["input"]["empirical_range"] == {"lower": 50, "upper": 50}
    assert row["output"]["estimated_tokens"] == 5
    exact = estimate([document("measured")], calibration)["sessions"][0]["models"][0]
    assert exact["input"]["status"] == "measured"
    assert exact["input"]["estimated_tokens"] is None


@pytest.mark.guard
def test_usage_estimation_session_copy_disjointness_and_conflicting_identity() -> None:
    data = cohort()
    plain = calibrate(data, cohort="synthetic")
    copies = calibrate(data + copy.deepcopy(data), cohort="synthetic")
    assert copies["cohort"]["sessions"] == plain["cohort"]["sessions"]
    assert copies["models"] == plain["models"]
    changed = copy.deepcopy(data[0])
    changed["source_digest"] = "f" * 64
    with pytest.raises(UsageEvidenceError, match="divergent"):
        admit_sessions(data + [changed])
    renamed = copy.deepcopy(data[0])
    renamed["session_id"] = "renamed-copy"
    with pytest.raises(UsageEvidenceError, match="copy"):
        admit_sessions(data + [renamed])
    uppercase = copy.deepcopy(data[0])
    uppercase["session_id"] = uppercase["session_id"].upper()
    assert len(admit_sessions([data[0], uppercase])) == 1
    assert admit_sessions([uppercase])[0]["session_id"] == data[0]["session_id"]


@pytest.mark.guard
def test_usage_estimation_review_regression_empty_transcripts_share_digest() -> None:
    """Two independent call-free (empty) transcripts both hash to SHA-256(empty);
    that must not be treated as a conflicting-copy identity collision."""
    empty_a = document("empty-a")
    empty_a["calls"] = []
    empty_a["source_digest"] = hashlib.sha256(b"").hexdigest()
    empty_b = document("empty-b")
    empty_b["calls"] = []
    empty_b["source_digest"] = hashlib.sha256(b"").hexdigest()
    admitted = admit_sessions([empty_a, empty_b])
    assert {item["session_id"] for item in admitted} == {"empty-a", "empty-b"}


@pytest.mark.guard
def test_usage_estimation_session_errors_not_call_percentiles() -> None:
    data = cohort()
    policy = CalibrationPolicy()
    for item in data:
        if is_validation(item["session_id"], policy):
            first = item["calls"][0]
            first.update(input_chars=10, input_tokens=10)
            second = dict(first, api_call_id="b", input_chars=90, input_tokens=40)
            item["calls"].append(second)
    result = calibrate(data, cohort="synthetic", policy=policy)
    assert result["models"]["model-a"]["input"]["held_out_relative_error"]["max"] == 0
    # Per-call errors are nonzero, but each held-out session totals 50 predicted/actual.


@pytest.mark.guard
def test_usage_estimation_poor_accuracy_disclosed_default_and_optional_ceiling() -> None:
    data = cohort()
    policy = CalibrationPolicy()
    for item in data:
        if is_validation(item["session_id"], policy):
            item["calls"][0]["input_tokens"] //= 10
    calibration = calibrate(data, cohort="synthetic")
    estimate_row = estimate([document("old", measured=False)], calibration)["sessions"][0]["models"][0]["input"]
    assert estimate_row["status"] == "estimated"
    assert estimate_row["held_out_relative_error"]["p90"] > 1
    assert "held-out-p90-error-at-least-100-percent" in estimate_row["warnings"]
    assert estimate_row["empirical_range"]["lower"] <= 5
    assert estimate_row["empirical_range"]["upper"] >= 50
    strict = calibrate(data, cohort="synthetic", policy=replace(policy, max_p90_relative_error=.25))
    row = estimate([document("old", measured=False)], strict)["sessions"][0]["models"][0]["input"]
    assert row["status"] == "unknown"
    assert row["reason"] == "configured-held-out-error-limit"


@pytest.mark.guard
def test_usage_estimation_unsupported_sparse_zero_and_extrapolation() -> None:
    sparse = calibrate(cohort()[:1], cohort="synthetic")
    row = estimate([document("old", measured=False)], sparse)["sessions"][0]["models"][0]
    assert row["input"]["reason"] == "insufficient-session-disjoint-samples"
    calibration = calibrate(cohort(), cohort="synthetic")
    old = document("old", measured=False, scale=100)
    row = estimate([old], calibration)["sessions"][0]["models"][0]
    assert "extrapolation-outside-training-applicability" in row["input"]["warnings"]
    old["calls"][0]["model"] = None
    row = estimate([old], calibration)["sessions"][0]["models"][0]
    assert row["input"]["reason"] == "unsupported-model"
    old["calls"][0]["model"] = "model-a"
    old["calls"][0]["input_chars"] = 0
    row = estimate([old], calibration)["sessions"][0]["models"][0]
    assert row["input"]["reason"] == "unobserved-features"
    data = cohort()
    for item in data:
        item["calls"][0]["output_tokens"] = 0
    assert calibrate(data, cohort="synthetic")["models"]["model-a"]["output"]["reason"] == "unusable-training-relationship"


@pytest.mark.guard
def test_usage_estimation_mixed_exact_and_missing_has_no_fabricated_total() -> None:
    item = document("mixed")
    item["calls"].append(dict(document("missing", measured=False)["calls"][0], api_call_id="b"))
    row = estimate([item], calibrate(cohort(), cohort="synthetic"))["sessions"][0]["models"][0]["input"]
    assert row["measured_tokens"] == row["estimated_tokens"] == 50
    assert "total_tokens" not in row
    assert row["missing_calls"] == row["measured_calls"] == 1
    assert any("missing-subsets" in warning for warning in row["warnings"])


@pytest.mark.guard
def test_usage_estimation_calibration_tampering_and_invalid_feature_admission() -> None:
    calibration = calibrate(cohort(), cohort="synthetic")
    calibration["models"]["model-a"]["input"]["coefficient"] *= 2
    with pytest.raises(UsageEvidenceError, match="revision"):
        estimate([document("old", measured=False)], calibration)
    calibration["calibration_id"] = _digest({key: value for key, value in calibration.items() if key != "calibration_id"})
    calibration["models"]["model-a"]["input"]["validation_sessions"] = 0
    calibration["calibration_id"] = _digest({key: value for key, value in calibration.items() if key != "calibration_id"})
    with pytest.raises(UsageEvidenceError, match="sample"):
        estimate([document("old", measured=False)], calibration)
    invalid = document("s")
    invalid["calls"][0]["input_chars"] = True
    with pytest.raises(UsageEvidenceError, match="count"):
        calibrate([invalid], cohort="synthetic")
    invalid = document("s")
    invalid["calls"].append(copy.deepcopy(invalid["calls"][0]))
    with pytest.raises(UsageEvidenceError, match="duplicate"):
        calibrate([invalid], cohort="synthetic")


@pytest.mark.guard
def test_usage_estimation_no_root_features_is_unknown_not_zero() -> None:
    empty = dict(document("empty"), calls=[])
    result = estimate([empty], calibrate(cohort(), cohort="synthetic"))["sessions"][0]
    assert result["status"] == "unknown"
    assert result["reason"] == "no-root-call-features"
    assert result["models"] == []


@pytest.mark.guard
def test_usage_estimation_cli_json_calibration_estimation_and_explicit_error(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    features = tmp_path / "features.json"
    features.write_text(json.dumps({"schema": "agent-logger.transcript-feature-set", "version": 1, "sessions": cohort()}))
    assert main(["usage", "calibrate", "--features", str(features), "--cohort", "synthetic"]) == 0
    calibration = tmp_path / "calibration.json"
    calibration.write_text(capsys.readouterr().out)
    features.write_text(json.dumps(document("historical", measured=False)))
    assert main(["usage", "estimate", "--features", str(features), "--calibration", str(calibration)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "agent-logger.transcript-estimates"
    assert result["sessions"][0]["models"][0]["input"]["estimated_tokens"] == 50
    features.write_text("{")
    assert main(["usage", "calibrate", "--features", str(features), "--cohort", "synthetic"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["schema"] == "agent-logger.usage-error"


@pytest.mark.guard
def test_usage_estimation_review_regression_estimate_range_is_int_and_encloses_point() -> None:
    """``estimated_tokens``/``empirical_range`` are integers, and the range encloses the point."""
    data = cohort()
    policy = CalibrationPolicy()
    for item in data:
        if is_validation(item["session_id"], policy):
            item["calls"][0]["input_tokens"] //= 10
    calibration = calibrate(data, cohort="synthetic")
    row = estimate(
        [document("old", measured=False)], calibration,
    )["sessions"][0]["models"][0]["input"]
    assert type(row["estimated_tokens"]) is int
    assert type(row["empirical_range"]["lower"]) is int
    assert type(row["empirical_range"]["upper"]) is int
    bounds = row["empirical_range"]
    assert bounds["lower"] <= row["estimated_tokens"] <= bounds["upper"]


@pytest.mark.guard
def test_usage_estimation_review_regression_calibration_requires_cohort_metadata() -> None:
    """A calibration missing ``source_cohort``/``range_interpretation`` is rejected up front,
    not left to raise an uncaught ``KeyError`` deep inside ``estimate()``."""
    calibration = calibrate(cohort(), cohort="synthetic")
    for field in ("source_cohort", "range_interpretation"):
        tampered = copy.deepcopy(calibration)
        del tampered[field]
        tampered["calibration_id"] = _digest({
            key: value for key, value in tampered.items() if key != "calibration_id"
        })
        with pytest.raises(UsageEvidenceError, match="incompatible"):
            estimate([document("old", measured=False)], tampered)


@pytest.mark.guard
def test_usage_estimation_review_regression_cli_reports_missing_calibration_metadata_as_json_error(
    tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    """The CLI must surface a missing calibration field as its documented JSON error
    (exit status 2), never an unhandled traceback."""
    calibration = calibrate(cohort(), cohort="synthetic")
    del calibration["source_cohort"]
    calibration["calibration_id"] = _digest({
        key: value for key, value in calibration.items() if key != "calibration_id"
    })
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration))
    features = tmp_path / "features.json"
    features.write_text(json.dumps(document("historical", measured=False)))
    args = [
        "usage", "estimate", "--features", str(features), "--calibration", str(calibration_path),
    ]
    assert main(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    error = json.loads(captured.err)
    assert error["schema"] == "agent-logger.usage-error"


@pytest.mark.guard
@pytest.mark.parametrize("malformed_models,match", [
    ({"model-a": ["input", "output"]}, "invalid calibration model"),
    (
        {"model-a": {"input": ["status"], "output": {"status": "unknown", "reason": "no-calls"}}},
        "metric",
    ),
])
def test_usage_estimation_review_regression_malformed_calibration_metrics_rejected(
    malformed_models: dict, match: str, tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    """A calibration whose ``models`` metrics are lists instead of dicts must raise
    the documented ``UsageEvidenceError``/CLI JSON error, never an uncaught
    ``AttributeError``/``TypeError`` traceback."""
    calibration = calibrate(cohort(), cohort="synthetic")
    calibration["models"] = malformed_models
    calibration["calibration_id"] = _digest({
        key: value for key, value in calibration.items() if key != "calibration_id"
    })
    with pytest.raises(UsageEvidenceError, match=match):
        estimate([document("old", measured=False)], calibration)
    calibration_path = tmp_path / "calibration.json"
    calibration_path.write_text(json.dumps(calibration))
    features = tmp_path / "features.json"
    features.write_text(json.dumps(document("historical", measured=False)))
    args = [
        "usage", "estimate", "--features", str(features), "--calibration", str(calibration_path),
    ]
    assert main(args) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["schema"] == "agent-logger.usage-error"
