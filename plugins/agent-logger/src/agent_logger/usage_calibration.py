"""Session-disjoint empirical calibration; no tokenizer or pricing defaults."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from statistics import median

from agent_logger.usage_feature_contract import admit_sessions
from agent_logger.usage_features import FEATURE_METHOD, FEATURE_VERSION, _digest
from agent_logger.usage_ledger import UsageEvidenceError

CALIBRATION_SCHEMA = "agent-logger.transcript-calibration"
CALIBRATION_VERSION = 1


@dataclass(frozen=True)
class CalibrationPolicy:
    min_train_sessions: int = 3
    min_validation_sessions: int = 2
    holdout_modulus: int = 5
    holdout_remainder: int = 0
    max_p90_relative_error: float | None = None

    def validate(self) -> None:
        for value in (self.min_train_sessions, self.min_validation_sessions):
            if type(value) is not int or not 1 <= value <= 1000:
                raise UsageEvidenceError("calibration session minimum is invalid")
        if (
            type(self.holdout_modulus) is not int or not 2 <= self.holdout_modulus <= 100
            or type(self.holdout_remainder) is not int
            or not 0 <= self.holdout_remainder < self.holdout_modulus
        ):
            raise UsageEvidenceError("calibration holdout policy is invalid")
        limit = self.max_p90_relative_error
        if limit is not None and (
            type(limit) not in (float, int) or not 0 <= limit <= 10**15 or not math.isfinite(limit)
        ):
            raise UsageEvidenceError("calibration accuracy policy is invalid")


def is_validation(session_id: str, policy: CalibrationPolicy) -> bool:
    number = int(hashlib.sha256(session_id.lower().encode()).hexdigest(), 16)
    return number % policy.holdout_modulus == policy.holdout_remainder


def _p90(values: list[float]) -> float:
    return sorted(values)[max(0, math.ceil(len(values) * .9) - 1)]


def _metric(sessions: list[dict], model: str, quantity: str, policy: CalibrationPolicy) -> dict:
    train: list[tuple[int, int, int]] = []
    validation: list[tuple[int, int, int]] = []
    ranges: list[int] = []
    rejected = available = 0
    incomplete_sessions = 0
    for session in sessions:
        candidates = [call for call in session["calls"] if call["model"] == model]
        pairs = [
            call for call in candidates
            if call["emissions"] > 0 and call[f"{quantity}_chars"] > 0
            and call[f"{quantity}_tokens"] is not None
        ]
        available += len(candidates)
        rejected += len(candidates) - len(pairs)
        incomplete_sessions += bool(pairs) and len(candidates) != len(pairs)
        if not pairs:
            continue
        sample = (
            sum(call[f"{quantity}_chars"] for call in pairs),
            sum(call[f"{quantity}_tokens"] for call in pairs),
            len(pairs),
        )
        if is_validation(session["session_id"], policy):
            validation.append(sample)
        else:
            train.append(sample)
            ranges.extend(call[f"{quantity}_chars"] for call in pairs)
    result = {
        "status": "unknown", "reason": "insufficient-session-disjoint-samples",
        "train_sessions": len(train), "validation_sessions": len(validation),
        "train_calls": sum(row[2] for row in train),
        "validation_calls": sum(row[2] for row in validation),
        "available_calls": available, "rejected_calls": rejected,
        "incomplete_measured_sessions": incomplete_sessions,
    }
    if len(train) < policy.min_train_sessions or len(validation) < policy.min_validation_sessions:
        return result
    coefficient = sum(row[1] for row in train) / sum(row[0] for row in train)
    if not math.isfinite(coefficient) or coefficient <= 0:
        result["reason"] = "unusable-training-relationship"
        return result
    errors = [abs(row[0] * coefficient - row[1]) / max(row[1], 1) for row in validation]
    ratios = [row[1] / (row[0] * coefficient) for row in validation]
    result.update({
        "coefficient": coefficient,
        "call_feature_range": [min(ranges), max(ranges)],
        "session_feature_range": [min(row[0] for row in train), max(row[0] for row in train)],
        "session_call_range": [min(row[2] for row in train), max(row[2] for row in train)],
        "held_out_relative_error": {"median": median(errors), "p90": _p90(errors), "max": max(errors)},
        "empirical_actual_over_predicted": {"min": min(ratios), "max": max(ratios)},
    })
    if policy.max_p90_relative_error is not None and _p90(errors) > policy.max_p90_relative_error:
        result["reason"] = "configured-held-out-error-limit"
        return result
    result.update(status="calibrated", reason=None)
    return result


def calibrate(
    documents: list[dict], *, cohort: str, policy: CalibrationPolicy | None = None,
) -> dict:
    policy = policy or CalibrationPolicy()
    policy.validate()
    if not isinstance(cohort, str) or not cohort.strip() or len(cohort) > 200:
        raise UsageEvidenceError("calibration source cohort label is required")
    sessions = admit_sessions(documents)
    models = sorted({call["model"] for session in sessions for call in session["calls"] if call["model"]})
    if len(models) > 1000:
        raise UsageEvidenceError("calibration model limit exceeded")
    result = {
        "schema": CALIBRATION_SCHEMA, "version": CALIBRATION_VERSION,
        "feature_version": FEATURE_VERSION, "feature_method": FEATURE_METHOD,
        "fit": "training sum(tokens) / sum(visible feature characters); zero intercept",
        "validation_unit": "session-model matched-call aggregate",
        "relative_error_denominator": "max(actual tokens, 1)",
        "range_interpretation": "held-out empirical extrema; not confidence intervals or guarantees",
        "policy": asdict(policy), "source_cohort": cohort,
        "cohort": {
            "sessions": len(sessions), "input_documents": len(documents),
            "train_sessions": sum(not is_validation(session["session_id"], policy) for session in sessions),
            "validation_sessions": sum(is_validation(session["session_id"], policy) for session in sessions),
            "revision": _digest([
                (session["session_id"], session["source_digest"], session["calls"]) for session in sessions
            ]),
        },
        "models": {model: {
            quantity: _metric(sessions, model, quantity, policy) for quantity in ("input", "output")
        } for model in models},
    }
    result["calibration_id"] = _digest(result)
    return result


def validate_calibration(document: object) -> dict:
    if (
        not isinstance(document, dict) or document.get("schema") != CALIBRATION_SCHEMA
        or type(document.get("version")) is not int or document["version"] != CALIBRATION_VERSION
        or type(document.get("feature_version")) is not int or document["feature_version"] != FEATURE_VERSION
        or document.get("feature_method") != FEATURE_METHOD
        or document.get("validation_unit") != "session-model matched-call aggregate"
        or not isinstance(document.get("source_cohort"), str)
        or not document["source_cohort"].strip() or len(document["source_cohort"]) > 200
        or not isinstance(document.get("range_interpretation"), str)
        or not document["range_interpretation"].strip()
        or not isinstance(document.get("models"), dict) or len(document["models"]) > 1000
    ):
        raise UsageEvidenceError("incompatible transcript calibration")
    try:
        expected = _digest({key: value for key, value in document.items() if key != "calibration_id"})
    except (ValueError, OverflowError) as exc:
        raise UsageEvidenceError("invalid calibration numeric encoding") from exc
    if document.get("calibration_id") != expected:
        raise UsageEvidenceError("calibration revision mismatch")
    try:
        policy = CalibrationPolicy(**document["policy"])
        policy.validate()
        for model, metrics in document["models"].items():
            if (
                not isinstance(model, str) or not model or len(model) > 2048
                or not isinstance(metrics, dict) or set(metrics) != {"input", "output"}
            ):
                raise UsageEvidenceError("invalid calibration model")
            for metric in metrics.values():
                if not isinstance(metric, dict):
                    raise UsageEvidenceError("invalid calibration metric")
                if metric["status"] == "unknown":
                    if not isinstance(metric["reason"], str):
                        raise UsageEvidenceError("calibration abstention reason is missing")
                    continue
                if (
                    metric["status"] != "calibrated"
                    or type(metric["coefficient"]) not in (int, float)
                    or not 0 < metric["coefficient"] <= 10**15
                ):
                    raise UsageEvidenceError("invalid calibration coefficient")
                for key, minimum in (
                    ("train_sessions", policy.min_train_sessions),
                    ("validation_sessions", policy.min_validation_sessions),
                ):
                    if type(metric[key]) is not int or not minimum <= metric[key] <= 1000:
                        raise UsageEvidenceError("invalid calibration sample count")
                for key in (
                    "train_calls", "validation_calls", "available_calls", "rejected_calls",
                    "incomplete_measured_sessions",
                ):
                    if type(metric[key]) is not int or not 0 <= metric[key] <= 20000:
                        raise UsageEvidenceError("invalid calibration coverage count")
                for key in ("call_feature_range", "session_feature_range", "session_call_range"):
                    bounds = metric[key]
                    if (
                        not isinstance(bounds, list) or len(bounds) != 2
                        or any(type(value) is not int for value in bounds)
                        or not 0 < bounds[0] <= bounds[1] <= 10**20
                    ):
                        raise UsageEvidenceError("invalid calibration applicability range")
                for key, fields in (
                    ("held_out_relative_error", ("median", "p90", "max")),
                    ("empirical_actual_over_predicted", ("min", "max")),
                ):
                    if set(metric[key]) != set(fields) or any(
                        type(value) not in (int, float) or not 0 <= value <= 10**40
                        or not math.isfinite(value)
                        for value in metric[key].values()
                    ):
                        raise UsageEvidenceError("invalid calibration error evidence")
                ratios = metric["empirical_actual_over_predicted"]
                if ratios["min"] > ratios["max"]:
                    raise UsageEvidenceError("invalid calibration empirical range")
                errors = metric["held_out_relative_error"]
                if not errors["median"] <= errors["p90"] <= errors["max"]:
                    raise UsageEvidenceError("invalid calibration error ordering")
                if (
                    policy.max_p90_relative_error is not None
                    and metric["held_out_relative_error"]["p90"] > policy.max_p90_relative_error
                ):
                    raise UsageEvidenceError("calibration violates its accuracy policy")
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise UsageEvidenceError("malformed transcript calibration") from exc
    return document
