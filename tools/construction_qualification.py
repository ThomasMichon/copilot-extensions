"""Pure construction-result admission; no execution, authentication or publication."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import PurePosixPath
import re


class QualificationError(ValueError):
    """Expected construction inputs or reported evidence are malformed."""


_NATIVE = {"os", "implementation", "python_version", "architecture", "abi", "hardware"}
_CHECKS = {"construction", "installed_origin", "installed_launch", "feed_policy", "cold_environment"}
_STATUSES = {"passed", "failed", "skipped", "timed-out", "unavailable"}


def _object(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise QualificationError(f"{label}: expected exactly {', '.join(sorted(keys))}")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise QualificationError(f"{label}: expected nonempty unpadded text")
    return value


def _digest(value: object, label: str, *, length: int = 64) -> str:
    if not isinstance(value, str) or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
        raise QualificationError(f"{label}: expected a lowercase {length}-digit digest")
    return value


def _version(record: dict, schema: str, label: str, *, version: int = 2) -> None:
    if record["schema"] != schema or type(record["version"]) is not int or record["version"] != version:
        raise QualificationError(f"{label}: unsupported schema or version")


def _native(value: object, label: str) -> dict:
    record = _object(value, _NATIVE, label)
    for key, item in record.items():
        _text(item, f"{label}.{key}")
    if re.fullmatch(r"3\.\d+\.\d+", record["python_version"]) is None:
        raise QualificationError(f"{label}: exact Python patch version required")
    return record


def _target_policy(policy: object) -> dict:
    policy = _object(policy, {"schema", "version", "python_minors", "targets"}, "target policy")
    _version(policy, "copilot-extensions.construction-targets", "target policy", version=1)
    minors = policy["python_minors"]
    if not isinstance(minors, list) or not minors or any(
        not isinstance(minor, str) or re.fullmatch(r"3\.(?:[1-9]\d*)", minor) is None
        for minor in minors
    ):
        raise QualificationError("target policy: expected explicit Python minor versions")
    numbers = [int(minor.split(".")[1]) for minor in minors]
    if numbers[0] < 11 or any(right != left + 1 for left, right in zip(numbers, numbers[1:])):
        raise QualificationError("target policy: Python 3.11+ minors must be contiguous, sorted and unique")
    if not isinstance(policy["targets"], list) or not policy["targets"]:
        raise QualificationError("target policy: expected nonempty native targets")
    identities: set[str] = set()
    selections: set[tuple[str, str, str]] = set()
    for target in policy["targets"]:
        target = _object(target, {"id", "os", "architecture", "hardware"}, "native target")
        for key, value in target.items():
            _text(value, f"native target.{key}")
        if target["os"] not in {"windows", "linux", "macos"} or target["architecture"] not in {"x64", "arm64"}:
            raise QualificationError("native target: unknown OS or architecture spelling")
        selection = (target["os"], target["architecture"], target["hardware"])
        if target["hardware"] not in {"generic", "raspberry-pi-5"} or (
            target["hardware"] == "raspberry-pi-5" and selection[:2] != ("linux", "arm64")
        ):
            raise QualificationError("native target: invalid hardware binding")
        if target["id"] in identities or selection in selections:
            raise QualificationError("target policy: duplicate native target")
        identities.add(target["id"])
        selections.add(selection)
    return policy


def _hash(record: dict) -> str:
    return hashlib.sha256(json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def target_policy_digest(policy: object) -> str:
    """Bind independently selected support policy, not the producer's available hosts."""
    policy = _target_policy(policy)
    return _hash(dict(policy, targets=sorted(policy["targets"], key=lambda target: target["id"])))


def _rows(plan: object) -> dict[str, dict]:
    record = _object(
        plan, {
            "schema", "version", "source_commit", "candidate_sha256", "policy_sha256",
            "native_policy_sha256", "rows",
        },
        "plan",
    )
    _version(record, "copilot-extensions.construction-plan", "plan")
    _digest(record["source_commit"], "plan.source_commit", length=40)
    _digest(record["candidate_sha256"], "plan.candidate_sha256")
    _digest(record["policy_sha256"], "plan.policy_sha256")
    _digest(record["native_policy_sha256"], "plan.native_policy_sha256")
    if not isinstance(record["rows"], list) or not record["rows"]:
        raise QualificationError("plan.rows: expected a nonempty complete environment matrix")
    rows: dict[str, dict] = {}
    selections: set[str] = set()
    for row in record["rows"]:
        row = _object(
            row,
            {"id", "project", "profile", "extras", "native", "construction_sha256", "installed_launch_sha256"},
            "plan row",
        )
        identity = _text(row["id"], "row.id")
        if identity in rows:
            raise QualificationError(f"duplicate plan row: {identity}")
        project = _text(row["project"], f"{identity}.project")
        path = PurePosixPath(project)
        if (
            "\\" in project or ":" in project or path.is_absolute() or ".." in path.parts
            or path.as_posix() != project or path.name != "pyproject.toml"
        ):
            raise QualificationError(f"{identity}: expected a canonical repository-relative project")
        _text(row["profile"], f"{identity}.profile")
        extras = row["extras"]
        if not isinstance(extras, list) or any(
            not isinstance(extra, str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", extra) is None
            for extra in extras
        ):
            raise QualificationError(f"{identity}: extras must be normalized names")
        if extras != sorted(set(extras)):
            raise QualificationError(f"{identity}: extras must be sorted and unique")
        _native(row["native"], f"{identity}.native")
        _digest(row["construction_sha256"], f"{identity}.construction_sha256")
        _digest(row["installed_launch_sha256"], f"{identity}.installed_launch_sha256")
        selection = json.dumps({key: value for key, value in row.items() if key != "id"}, sort_keys=True)
        if selection in selections:
            raise QualificationError(f"duplicate environment selection: {identity}")
        selections.add(selection)
        rows[identity] = row
    return rows


def plan_digest(plan: object) -> str:
    """Bind a trusted expected matrix; row ordering is not an input difference."""
    rows = _rows(plan)
    canonical = dict(plan, rows=[rows[key] for key in sorted(rows)])
    return _hash(canonical)


def _matrix_reasons(rows: dict[str, dict], policy: dict) -> list[str]:
    targets = {
        (target["os"], target["architecture"], target["hardware"]): target["id"]
        for target in policy["targets"]
    }
    required = {
        (*target, minor) for target in targets for minor in policy["python_minors"]
    }
    environments: dict[tuple[str, str, tuple[str, ...]], set[tuple[str, str, str, str]]] = {}
    reasons: list[str] = []
    for identity, row in rows.items():
        environment = (row["project"], row["profile"], tuple(row["extras"]))
        seen = environments.setdefault(environment, set())
        native = row["native"]
        minor = ".".join(native["python_version"].split(".")[:2])
        selection = (native["os"], native["architecture"], native["hardware"], minor)
        if selection in seen:
            reasons.append(f"{identity}:duplicate-native-target")
        seen.add(selection)
        if selection not in required:
            reasons.append(f"{identity}:unexpected-native-target")
        if native["implementation"] != "cpython" or native["abi"] != "cp" + minor.replace(".", ""):
            reasons.append(f"{identity}:nonstandard-python-abi")
    for environment, seen in sorted(environments.items()):
        project, profile, extras = environment
        label = f"{project}:{profile}:{','.join(extras)}"
        for *target, minor in sorted(required - seen):
            reasons.append(f"{label}:missing-target:{targets[tuple(target)]}:python-{minor}")
    return reasons


def assess_qualification(expected_plan: object, report: object, *, target_policy: object) -> dict:
    """Check exact coverage and successful evidence against independent expected inputs.

    The caller owns complete plan derivation and authenticated report acquisition.
    Matching hashes validate binding, not provenance or truth of execution.
    """
    rows = _rows(expected_plan)
    expected_digest = plan_digest(expected_plan)
    target_policy = _target_policy(target_policy)
    target_digest = target_policy_digest(target_policy)
    report = _object(report, {"schema", "version", "plan_sha256", "results"}, "report")
    _version(report, "copilot-extensions.construction-results", "report")
    _digest(report["plan_sha256"], "report.plan_sha256")
    if not isinstance(report["results"], list):
        raise QualificationError("report.results: expected an array")
    reasons = _matrix_reasons(rows, target_policy)
    if expected_plan["native_policy_sha256"] != target_digest:
        reasons.append("native-policy-binding-mismatch")
    if report["plan_sha256"] != expected_digest:
        reasons.append("plan-binding-mismatch")
    seen: set[str] = set()
    for result in report["results"]:
        result = _object(
            result, {"row_id", "native", "status", "detail", "duration_seconds", "checks"}, "result",
        )
        identity = _text(result["row_id"], "result.row_id")
        if identity in seen:
            raise QualificationError(f"duplicate result row: {identity}")
        seen.add(identity)
        status = _text(result["status"], f"{identity}.status")
        if status not in _STATUSES:
            raise QualificationError(f"{identity}: unknown result status")
        if not isinstance(result["detail"], str) or (status != "passed" and not result["detail"].strip()):
            raise QualificationError(f"{identity}: unsuccessful results need a diagnostic detail")
        duration = result["duration_seconds"]
        if (
            type(duration) not in (int, float) or duration < 0
            or (type(duration) is float and not math.isfinite(duration))
        ):
            raise QualificationError(f"{identity}: duration must be finite and nonnegative")
        if result["native"] is not None:
            _native(result["native"], f"{identity}.native")
        checks = _object(result["checks"], _CHECKS, f"{identity}.checks")
        for name, check in sorted(checks.items()):
            check = _object(check, {"status", "evidence_sha256"}, f"{identity}.{name}")
            if not isinstance(check["status"], str) or check["status"] not in _STATUSES:
                raise QualificationError(f"{identity}.{name}: unknown check status")
            if check["evidence_sha256"] is not None:
                _digest(check["evidence_sha256"], f"{identity}.{name}.evidence_sha256")
            if check["status"] != "passed" or check["evidence_sha256"] is None:
                reasons.append(f"{identity}:{name}-unqualified")
        if identity not in rows:
            reasons.append(f"{identity}:unexpected-row")
        elif result["native"] != rows[identity]["native"]:
            reasons.append(f"{identity}:native-binding-mismatch")
        if status != "passed":
            reasons.append(f"{identity}:{status}")
    reasons.extend(f"{identity}:missing-row" for identity in sorted(set(rows) - seen))
    return {
        "schema": "copilot-extensions.construction-assessment",
        "version": 2,
        "plan_sha256": expected_digest,
        "qualified": not reasons,
        "expected_rows": len(rows),
        "reported_rows": len(seen),
        "reasons": sorted(reasons),
    }
