"""Operation-time incarnation proof, never a replacement creation receipt."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

import yaml

from . import config as cfg
from .checkout_relocation_state import LEGACY_SCHEMA as SCHEMA

if TYPE_CHECKING:
    from .tracking import WorktreeRecord


def _valid_incarnation(value: object) -> bool:
    return (
        isinstance(value, dict) and set(value) == {"device", "inode", "birth_ns"}
        and all(type(item) is int and item >= 0 for item in value.values())
        and value["inode"] > 0
    )


def validate(value: object) -> None:
    if not isinstance(value, dict) or (
        set(value) - {"version", "namespace", "ledger", "records", "source", "common", "administrative", "destination"}
        or type(value.get("version")) is not int or value["version"] != 1
        or not isinstance(value.get("namespace"), dict)
        or set(value["namespace"]) != {"hostname", "environment"}
        or any(not isinstance(item, str) or not item for item in value["namespace"].values())
        or not isinstance(value.get("ledger"), str) or not Path(value["ledger"]).is_absolute()
        or not isinstance(value.get("records"), list) or not 1 <= len(value["records"]) <= 2
        or not _valid_incarnation(value.get("source"))
        or "destination" in value and not _valid_incarnation(value["destination"])
    ):
        raise ValueError("unsupported legacy relocation identity witness")
    for snapshot in value["records"]:
        if (
            not isinstance(snapshot, dict) or set(snapshot) != {"incarnation", "sha256"}
            or not _valid_incarnation(snapshot.get("incarnation"))
            or not isinstance(snapshot.get("sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", snapshot["sha256"]) is None
        ):
            raise ValueError("invalid legacy relocation ledger witness")
    for key in ("common", "administrative"):
        part = value.get(key)
        if (
            not isinstance(part, dict) or set(part) != {"path", "incarnation"}
            or not isinstance(part.get("path"), str) or not Path(part["path"]).is_absolute()
            or not _valid_incarnation(part.get("incarnation"))
        ):
            raise ValueError("invalid legacy relocation Git witness")


def _incarnation(path: Path, *, directory: bool = False) -> dict[str, int]:
    before = path.lstat()
    if (
        not (stat.S_ISDIR(before.st_mode) if directory else stat.S_ISREG(before.st_mode))
        or not directory and before.st_nlink != 1
        or path.is_symlink() or getattr(before, "st_file_attributes", 0) & 0x400
        or path.absolute() != path.resolve() or not before.st_ino
    ):
        raise ValueError("legacy relocation incarnation is redirected or unsupported")
    return {
        "device": before.st_dev, "inode": before.st_ino,
        "birth_ns": getattr(before, "st_birthtime_ns", before.st_ctime_ns),
    }


def _ledger(path: Path, *, terminal: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    from .checkout_relocation_plan import read_snapshot

    before = _incarnation(path)
    raw = read_snapshot(path, max_bytes=None if terminal else 64 * 1024)
    data = yaml.safe_load(raw)
    if (
        _incarnation(path) != before
        or not isinstance(data, dict)
    ):
        raise ValueError("legacy relocation receiving ledger changed during inspection")
    return data, {"incarnation": before, "sha256": hashlib.sha256(raw).hexdigest()}


def birth_absent(record: WorktreeRecord, ledger: Path) -> bool:
    data, _snapshot = _ledger(ledger)
    if (
        data.get("worktree_id") != record.worktree_id or data.get("repo") != record.repo
        or data.get("branch") != record.branch or data.get("worktree_path") != record.worktree_path
    ):
        raise ValueError("legacy relocation snapshot conflicts with its receiving ledger")
    return "creation_nonce" not in data and not record.creation_nonce


def inspect_origin(record: WorktreeRecord, ledger: Path, *, lock_snapshot: bool) -> None:
    from .receiving_allocation import has_retained_receiving_operation

    if has_retained_receiving_operation(
        record, ledger.parent, lock_snapshot=lock_snapshot, unversioned=True,
    ):
        raise ValueError("unversioned checkout retains original creation/receiving evidence; inspect through its owner")


def _namespace(project: str) -> dict[str, str]:
    from . import record_reporting

    context = record_reporting.current_context(project)
    platform = cast(Callable[[], str], getattr(cfg, "detect_platform"))()
    if getattr(context, "unavailable", False) or context.environment != platform:
        raise ValueError("legacy relocation receiving execution is unavailable")
    return {"hostname": context.hostname.casefold(), "environment": context.environment}


def capture(record: WorktreeRecord, ledger: Path, config: cfg.Config) -> dict[str, Any]:
    from . import retained_checkout

    if not birth_absent(record, ledger):
        raise ValueError("legacy relocation requires genuinely absent birth metadata")
    expected = cfg.tracking_dir(config.repo_name) / f"{record.worktree_id}.yaml"
    if ledger != expected or record.repo != config.repo_name:
        raise ValueError("legacy relocation requires its exact receiving ledger")
    source, anchor = Path(record.worktree_path), Path(config.default_repo.anchor)
    head = retained_checkout._require_linked_checkout(source, anchor)
    if head != f"ref: refs/heads/{record.branch}":
        raise ValueError("legacy relocation Git branch conflicts with its ledger")
    common = retained_checkout._git_directory(anchor)
    administrative = retained_checkout._gitdir_marker(source)
    _data, snapshot = _ledger(ledger)
    return {
        "version": 1, "namespace": _namespace(config.repo_name), "ledger": str(ledger),
        "records": [snapshot], "source": _incarnation(source, directory=True),
        "common": {"path": str(common), "incarnation": _incarnation(common, directory=True)},
        "administrative": {
            "path": str(administrative), "incarnation": _incarnation(administrative, directory=True),
        },
    }


def require(
    record: WorktreeRecord, ledger: Path, report: dict[str, Any], config: cfg.Config | None = None,
    *, location_only: bool = False,
) -> None:
    from . import retained_checkout

    witness = report.get("legacy_identity")
    validate(witness)
    if not isinstance(witness, dict):
        raise ValueError("unsupported legacy relocation identity witness")
    data, snapshot = _ledger(ledger, terminal=report["phase"] in {"complete", "cancelled"})
    if (
        witness.get("ledger") != str(ledger) or snapshot not in witness.get("records", [])
        or "creation_nonce" in data or record.creation_nonce
    ):
        raise ValueError("legacy relocation receiving ledger incarnation changed")
    locations = {report["destination"]} if report["phase"] in {
        "rebound", "retiring", "complete",
    } else {report["source"], report["destination"]} if report["phase"] == "git-repaired" else {report["source"]}
    if data.get("worktree_path") not in locations:
        raise ValueError("legacy relocation ledger location rolled back across its checkpoint")
    if witness["namespace"] != _namespace(record.repo):
        raise ValueError("legacy relocation receiving execution changed")
    if location_only and report["phase"] in {"complete", "cancelled"}:
        return
    for key in ("common", "administrative"):
        part = witness.get(key)
        if not isinstance(part, dict) or not isinstance(part.get("path"), str) or (
            _incarnation(Path(part["path"]), directory=True) != part.get("incarnation")
        ):
            raise ValueError("legacy relocation Git administration incarnation changed")
    anchor = Path(config.default_repo.anchor) if config is not None else Path(report["anchor"])
    if (
        str(retained_checkout._git_directory(anchor)) != witness["common"]["path"]
    ):
        raise ValueError("legacy relocation receiving execution/Git family changed")
    source, target = Path(report["source"]), Path(report["destination"])
    if report["phase"] != "complete" and source.exists() and (
        _incarnation(source, directory=True) != witness["source"]
        or retained_checkout._gitdir_marker(source) != Path(witness["administrative"]["path"])
    ):
        raise ValueError("legacy relocation source incarnation changed")
    if report["phase"] != "cancelled" and target.exists() and (
        _incarnation(target, directory=True) != witness.get("destination")
        or retained_checkout._gitdir_marker(target) != Path(witness["administrative"]["path"])
    ):
        raise ValueError("legacy relocation destination incarnation changed")
    if "stage" in report:
        stage = Path(report["stage"])
        if report["phase"] in {"copying", "copied", "cancelling"} and (
            stage.exists() or stage.is_symlink()
        ):
            require_stage(report, stage)
        retirement = source.with_name(
            source.name + ".retiring-" + stage.name.rsplit("-", 1)[1],
        )
        if report["phase"] not in {"complete", "cancelled"} and (
            retirement.exists() and _incarnation(retirement, directory=True) != witness.get("source")
        ):
            raise ValueError("legacy relocation retirement incarnation changed")
    candidates = [target] if report["phase"] in {
        "git-repaired", "rebound", "retiring", "complete",
    } else [source, target] if report["phase"] == "published" else [source]
    for checkout in candidates:
        try:
            branch = retained_checkout._require_linked_checkout(checkout, anchor)
        except (OSError, ValueError):
            continue
        if report["phase"] not in {"complete", "cancelled"} and branch != f"ref: refs/heads/{record.branch}":
            continue
        if report["phase"] not in {"complete", "cancelled"}:
            from .checkout_relocation import _git

            if _git(checkout, "rev-parse", "HEAD") != report["head"]:
                raise ValueError("legacy relocation Git HEAD changed")
        break
    else:
        raise ValueError("legacy relocation reciprocal Git membership changed")


def require_stage(report: dict[str, Any], stage: Path) -> None:
    if report["schema"] != SCHEMA:
        return
    expected = report["legacy_identity"].get("destination")
    actual = _incarnation(stage, directory=True)
    # Native metadata preservation can change directory timestamps, not its file ID.
    if not isinstance(expected, dict) or any(
        actual[key] != expected.get(key) for key in ("device", "inode")
    ):
        raise ValueError("legacy relocation stage incarnation changed")


def retain_destination(report: dict[str, Any], stage: Path) -> None:
    if report["schema"] == SCHEMA:
        if "destination" in report["legacy_identity"]:
            require_stage(report, stage)
        report["legacy_identity"]["destination"] = _incarnation(stage, directory=True)


def attribution_identity(record: WorktreeRecord, ledger: Path) -> str | None:
    """Keep attribution stable without turning current-location proof into birth metadata."""
    from . import checkout_relocation_state as state

    report = state.read(ledger)
    if report is None or report["schema"] != SCHEMA:
        return None
    require(record, ledger, report, location_only=True)
    witness = report["legacy_identity"]
    identity = {key: witness[key] for key in ("namespace", "ledger", "common", "administrative")}
    return "legacy:" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def prepare_record_replace(ledger: Path, staging: Path) -> None:
    """Journal the next ledger's actual incarnation before its atomic publication."""
    from . import checkout_relocation_state as state, tracking

    if ledger.suffix != ".yaml":
        return
    report = state.read(ledger)
    if report is None or report["schema"] != SCHEMA:
        return
    current = tracking._load_record_uncached(ledger)
    require(current, ledger, report, location_only=True)
    terminal = report["phase"] in {"complete", "cancelled"}
    data, upcoming = _ledger(staging, terminal=terminal)
    if (
        "creation_nonce" in data or data.get("worktree_id") != current.worktree_id
        or data.get("repo") != current.repo
        or data.get("worktree_path") not in {report["source"], report["destination"]}
        or report["phase"] not in {"complete", "cancelled"} and not state.owns(ledger)
    ):
        raise ValueError("legacy relocation record publication conflicts with its operation")
    _old, snapshot = _ledger(ledger, terminal=terminal)
    report["legacy_identity"]["records"] = [snapshot, upcoming]
    state.write(ledger, report)
