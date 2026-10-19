"""Separately labeled measured quantities and empirical historical estimates."""

from __future__ import annotations

import math

from agent_logger.usage_calibration import validate_calibration
from agent_logger.usage_feature_contract import admit_sessions


def _estimate_metric(calls: list[dict], quantity: str, metric: dict | None) -> dict:
    exact = [call for call in calls if call[f"{quantity}_tokens"] is not None]
    missing = [call for call in calls if call[f"{quantity}_tokens"] is None]
    result: dict = {
        "measured_tokens": sum(call[f"{quantity}_tokens"] for call in exact) if exact else None,
        "measured_calls": len(exact), "missing_calls": len(missing),
        "estimated_tokens": None, "empirical_range": None, "warnings": [],
    }
    if not missing:
        return dict(result, status="measured" if exact else "unknown", reason=None if exact else "no-calls")
    if not metric or metric["status"] != "calibrated":
        return dict(result, status="unknown", reason=metric["reason"] if metric else "unsupported-model")
    if any(call[f"{quantity}_chars"] == 0 or call["emissions"] == 0 for call in missing):
        return dict(result, status="unknown", reason="unobserved-features")
    feature_total = sum(call[f"{quantity}_chars"] for call in missing)
    predicted = feature_total * metric["coefficient"]
    ratios = metric["empirical_actual_over_predicted"]
    # Token quantities are integer counts: round the point estimate to the
    # nearest whole token, and floor/ceil the empirical bounds outward so they
    # still enclose that rounded point estimate.
    estimated_tokens = round(predicted)
    lower = min(math.floor(predicted * min(1, ratios["min"])), estimated_tokens)
    upper = max(math.ceil(predicted * max(1, ratios["max"])), estimated_tokens)
    result.update(
        estimated_tokens=estimated_tokens,
        empirical_range={"lower": lower, "upper": upper},
        status="estimated", reason=None,
        confidence="empirical-only",
        held_out_relative_error=metric["held_out_relative_error"],
        calibration_train_sessions=metric["train_sessions"],
        calibration_validation_sessions=metric["validation_sessions"],
    )
    low, high = metric["call_feature_range"]
    session_low, session_high = metric["session_feature_range"]
    count_low, count_high = metric["session_call_range"]
    if (
        any(not low <= call[f"{quantity}_chars"] <= high for call in missing)
        or not session_low <= feature_total <= session_high
        or not count_low <= len(missing) <= count_high
    ):
        result["warnings"].append("extrapolation-outside-training-applicability")
    if metric["held_out_relative_error"]["p90"] >= 1:
        result["warnings"].append("held-out-p90-error-at-least-100-percent")
    if metric["incomplete_measured_sessions"]:
        result["warnings"].append("calibration-has-incomplete-measured-session-coverage")
    if exact:
        result["warnings"].append("range-calibrated-on-matched-call-session-aggregates-not-missing-subsets")
    return result


def estimate(documents: list[dict], calibration: dict) -> dict:
    calibration = validate_calibration(calibration)
    sessions = admit_sessions(documents)
    output = []
    for session in sessions:
        models = sorted({call["model"] for call in session["calls"]}, key=lambda item: item or "")
        groups = []
        for model in models:
            calls = [call for call in session["calls"] if call["model"] == model]
            metrics = calibration["models"].get(model, {})
            groups.append({
                "model": model, "calls": len(calls),
                **{quantity: _estimate_metric(calls, quantity, metrics.get(quantity)) for quantity in ("input", "output")},
            })
        output.append({
            "session_id": session["session_id"], "source_digest": session["source_digest"],
            "models": groups, "feature_warnings": session.get("warnings", []),
            "status": "reported" if groups else "unknown",
            "reason": None if groups else "no-root-call-features",
        })
    return {
        "schema": "agent-logger.transcript-estimates", "version": 1,
        "calibration_id": calibration["calibration_id"],
        "source_cohort": calibration["source_cohort"], "sessions": output,
        "range_interpretation": calibration["range_interpretation"],
        "limitations": "not measured usage, cache buckets, AI credits, payer attribution or invoices; no historical accuracy guarantee",
    }
