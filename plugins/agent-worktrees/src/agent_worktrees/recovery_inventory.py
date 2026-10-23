"""Bounded exact-input recovery observations, never migration or admission."""

from __future__ import annotations

import json
import stat
from pathlib import Path

from .recovery_facts import (
    MAX_BYTES, RecoveryError, _constant, _depth, _integer, _number, _object,
    decode_fact, identity,
)
from .recovery_markers import _ordinary_chain, inspect_layout

MAX_FACTS = 32
MAX_SELECTION = 256
REQUEST_SCHEMA = "agent-worktrees.recovery-report-request"


def read_bounded(path: Path, limit: int) -> bytes:
    """Read one ordinary explicit file, without discovery or repair."""
    _root(str(path))
    try:
        _ordinary_chain(path.parent)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise RecoveryError("path_indirection")
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        raise RecoveryError("source_missing") from None
    except OSError:
        raise RecoveryError("source_unavailable") from None
    if len(raw) > limit:
        raise RecoveryError("record_limit")
    return raw


def _root(value: object) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise RecoveryError("invalid_root")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise RecoveryError("invalid_root") from None
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise RecoveryError("invalid_root")
    return path


def parse_request(raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise RecoveryError("record_limit")
    try:
        value = json.loads(
            raw, object_pairs_hook=_object, parse_constant=_constant,
            parse_float=_number, parse_int=_integer,
        )
        _depth(value)
    except (UnicodeError, json.JSONDecodeError):
        raise RecoveryError("malformed_request") from None
    except RecursionError:
        raise RecoveryError("record_limit") from None
    if (
        not isinstance(value, dict) or value.get("schema") != REQUEST_SCHEMA
        or type(value.get("version")) is not int or value["version"] != 1
    ):
        raise RecoveryError("unsupported_schema")
    if value.get("required_capabilities", []) != []:
        raise RecoveryError("required_capability_unsupported")
    identity(value.get("repository_id"))
    identity(value.get("resource_id"))
    _root(value.get("worktree_root"))
    _root(value.get("git_common_dir"))
    if value.get("central_record_path") is not None:
        _root(value["central_record_path"])
    if value.get("marker_ignored") is not True:
        raise RecoveryError("marker_not_ignored")
    files = value.get("fact_files", [])
    budget = value.get("fact_budget", 8)
    if (
        not isinstance(files, list) or len(files) > MAX_SELECTION
        or type(budget) is not int or not 0 <= budget <= MAX_FACTS
    ):
        raise RecoveryError("invalid_selection")
    for name in files:
        if not isinstance(name, str) or not name.endswith(".json"):
            raise RecoveryError("invalid_selection")
        identity(name[:-5])
    if len(set(files)) != len(files):
        raise RecoveryError("invalid_selection")
    return value


def finding(reason: str) -> dict:
    if reason in {"identity_conflict", "foreign_path_conflict", "path_indirection"}:
        status = "conflict"
    elif reason in {"unsupported_schema", "required_capability_unsupported", "number_precision_unsupported"}:
        status = "unsupported"
    elif reason == "source_missing":
        status = "missing"
    else:
        status = "unknown"
    return {"status": status, "reason": reason}


def empty_report() -> dict:
    return {
        "schema": "agent-worktrees.recovery-report", "version": 1,
        "mode": "report-only", "outcome": "unknown",
        "migration_completeness": "unknown", "migration_reason": "g2_manifest_unavailable",
        "obligations": "unknown", "writer_authorized": False,
        "effects_admitted": False, "discovery": "none",
        "facts": [], "facts_checked": 0, "facts_omitted": 0,
    }


def _cache_observation(path: Path) -> dict:
    try:
        _ordinary_chain(path.parent)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise RecoveryError("path_indirection")
    except FileNotFoundError:
        return finding("source_missing")
    except OSError:
        return finding("source_unavailable")
    except RecoveryError as exc:
        return finding(exc.reason)
    return {"status": "unknown", "reason": "central_record_unexamined"}


def inventory(request: dict) -> dict:
    """Inspect one already-validated request; no semantic-completeness claim.

    Marker presence and caller assertions cannot certify a G2 migration. This
    consumer registers no semantic reader capabilities and never replays facts.
    """
    report = empty_report()
    files = request.get("fact_files", [])
    budget = request.get("fact_budget", 8)
    report["discovery"] = "explicit-bounded-file-selection" if files else "none"
    report["facts_omitted"] = max(0, len(files) - budget)
    cache = request.get("central_record_path")
    report["central_record"] = (
        _cache_observation(Path(cache)) if cache else
        {"status": "unknown", "reason": "central_record_not_selected"}
    )
    try:
        layout = inspect_layout(
            Path(request["worktree_root"]), Path(request["git_common_dir"]),
            repository_id=request["repository_id"], resource_id=request["resource_id"],
            marker_ignored=request["marker_ignored"],
        )
        # G1's absent-path preview is not evidence that anchors actually survive.
        present = layout.marker_directory.is_dir() and layout.journal_directory.is_dir()
        report["anchors"] = (
            {"status": "candidate", "reason": "correlated_markers"}
            if present else finding("source_missing")
        )
    except RecoveryError as exc:
        report["anchors"] = finding(exc.reason)
        report["facts_omitted"] = len(files)
    except OSError:
        report["anchors"] = finding("source_unavailable")
        report["facts_omitted"] = len(files)
    else:
        for name in files[:budget]:
            try:
                fact = decode_fact(
                    read_bounded(layout.journal_directory / name, MAX_BYTES),
                    supported=frozenset(),
                )
                if (
                    fact["repository_id"] != request["repository_id"]
                    or fact["resource_id"] != request["resource_id"]
                    or fact["operation_id"] != name[:-5]
                ):
                    raise RecoveryError("identity_conflict")
                item = {"status": "candidate", "reason": "envelope_readable"}
            except RecoveryError as exc:
                item = finding(exc.reason)
            report["facts"].append({"name": name, **item})
        report["facts_checked"] = len(report["facts"])
    statuses = {report["anchors"]["status"], report["central_record"]["status"]}
    statuses.update(item["status"] for item in report["facts"])
    report["outcome"] = (
        "conflict" if "conflict" in statuses else
        "unsupported" if "unsupported" in statuses else
        "partial" if "candidate" in statuses else "unknown"
    )
    return report
