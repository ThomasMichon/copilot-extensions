"""Explicit same-native-machine label repair, without moving worktree identity."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import NamedTuple

import yaml
from machine_transport import resolve_machine_selection

from . import config as cfg, execution_spaces, git_ops, output, pr_authority, repos, tracking
from . import tracking_yaml_repair
from .config_cache import ConfigCacheSession


class Inspection(NamedTuple):
    config: cfg.Config
    path: Path
    original: bytes
    data: dict
    proof: str


class RepairAuditError(RuntimeError):
    """The record replacement committed but the final receipt did not."""

    def __init__(self, backup: Path, audit: Path):
        super().__init__(f"machine label repaired but final audit failed: {audit}; backup: {backup}")
        self.backup, self.audit = backup, audit


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _binding(config) -> tuple:
    return (
        config.machine, config.platform, config.repo_name,
        _path_key(Path(config.default_repo.anchor)),
        _path_key(Path(config.default_repo.worktree_root)),
    )


def _prove_local_alias(config, previous: str) -> None:
    platform = cfg.detect_platform()
    if platform not in ("windows", "linux") or config.platform != platform:
        raise ValueError("machine-label repair requires the current native execution platform")
    execution_spaces.require_current_execution_space(config)
    entries = execution_spaces.registry_for_config(config)
    selected = resolve_machine_selection(entries, config.machine, platform)
    if not entries:
        if previous.casefold() != socket.gethostname().casefold():
            raise ValueError("previous label is not the observed local hostname")
        return
    current = selected.entry
    old = resolve_machine_selection(entries, previous, platform).entry
    observed = resolve_machine_selection(entries, socket.gethostname(), platform).entry
    if current is None or old is None or observed is None or not (
        current.key == old.key == observed.key
    ):
        raise ValueError("previous, configured and observed identities are not one local machine")
    if current.execution_platform and current.execution_platform != platform:
        raise ValueError("machine-label repair cannot cross execution platforms")


def _prove_checkout(data: dict, config) -> None:
    if data.get("repo") != config.repo_name or data.get("platform") != config.platform:
        raise ValueError("record project/platform does not match the selected local project")
    checkout = Path(data.get("worktree_path") or "")
    if not checkout.is_absolute() or not (checkout / ".git").is_file():
        raise ValueError("record must name an existing local linked worktree")
    if checkout.resolve() != checkout.absolute():
        raise ValueError("worktree path indirection refused")
    result = git_ops.git(
        "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir",
        cwd=checkout, isolated_repository=True, timeout=10,
    )
    paths = result.stdout.strip().splitlines()
    anchor = git_ops.git(
        "rev-parse", "--path-format=absolute", "--git-common-dir",
        cwd=config.default_repo.anchor, isolated_repository=True, timeout=10,
    ).stdout.strip()
    if len(paths) != 2 or (
        os.path.normcase(str(Path(paths[0]).resolve())) != os.path.normcase(str(checkout.resolve()))
        or os.path.normcase(str(Path(paths[1]).resolve())) != os.path.normcase(str(Path(anchor).resolve()))
    ):
        raise ValueError("record checkout is not registered to the selected project repository")


def _prove_history(data: dict, config) -> None:
    """A removed checkout needs independent receiving-registration evidence."""
    entries = execution_spaces.registry_for_config(config)
    current = entries.get(config.machine)
    if (
        current is None or current.key != config.machine or current.legacy_key
        or current.execution_platform != cfg.detect_platform()
    ):
        raise ValueError("removed checkout requires the configured explicit native execution-space key")
    registered = repos.read_registry(strict=True).repos.get(config.repo_name)
    # Both are established canonical classes validated by repos.VALID_CLASSES.
    if registered is None or registered.repo_class not in ("worktree", "knowledge"):
        raise ValueError("removed checkout requires canonical worktree/knowledge registration")
    local_root = registered.local_path(config.platform)
    anchor = Path(config.default_repo.anchor)
    if not local_root or _path_key(Path(local_root)) != _path_key(anchor):
        raise ValueError("receiving project does not match its registered native repository")
    checkout = Path(data.get("worktree_path") or "")
    root = Path(config.default_repo.worktree_root)
    if not checkout.is_absolute() or not root.is_absolute():
        raise ValueError("removed checkout requires absolute checkout and namespace paths")
    if checkout.is_symlink():
        raise ValueError("removed checkout path indirection refused")
    if checkout.exists():
        raise ValueError("historical proof requires an absent checkout; inspect its current Git state")
    if root.exists() and not root.is_dir():
        raise ValueError("registered worktree namespace is not a directory")
    expected = root / data["worktree_id"]
    if os.path.normcase(str(checkout)) != os.path.normcase(str(expected)):
        raise ValueError("removed checkout is outside the registered worktree namespace")
    # Do not resolve the expected final component: resolving both sides would
    # hide a dangling directory redirect at the record ID.
    if _path_key(checkout) != os.path.normcase(str(root.resolve() / data["worktree_id"])):
        raise ValueError("removed checkout has a redirected namespace descendant")
    git_ops.git(
        "rev-parse", "--path-format=absolute", "--git-common-dir",
        cwd=anchor, isolated_repository=True, timeout=10,
    )


def _inspect(project: str, record_id: str, previous: str, target: str | None):
    with ConfigCacheSession().scope():
        config = cfg.load_config(include_control_plane_related_pr=False)
    if config.repo_name != project:
        raise ValueError("resolved project does not match the selected project")
    if target is not None and target != config.machine:
        raise ValueError("--to-machine must exactly match the configured local target")
    _prove_local_alias(config, previous)
    path = cfg.tracking_dir() / f"{record_id}.yaml"
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError("tracking record path indirection refused")
    observed = path.read_bytes()
    data = tracking_yaml_repair.strict_mapping(observed.decode("utf-8"))
    if data.get("worktree_id") != record_id:
        raise ValueError("record identity does not match the selected worktree")
    if data.get("machine") not in (previous, config.machine):
        raise ValueError("record machine does not match --from-machine or the local target")
    if data.get("repo") != project or data.get("platform") != config.platform:
        raise ValueError("record project/platform does not match the selected local project")
    checkout = Path(data.get("worktree_path") or "")
    if checkout.is_symlink():
        raise ValueError("worktree path indirection refused")
    if checkout.exists():
        _prove_checkout(data, config)
        proof = "existing-linked-git"
    else:
        _prove_history(data, config)
        proof = "removed-registered-namespace"
    if data["machine"] == config.machine:
        for audit in path.parent.glob(f"{path.name}.machine-label-*.json"):
            if audit.is_symlink():
                raise ValueError("machine-label audit path indirection refused")
            receipt = json.loads(audit.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or receipt.get("state") != "committed":
                raise ValueError(f"prior machine-label repair has an incomplete audit: {audit}")
    return Inspection(config=config, path=path, original=observed, data=data, proof=proof)


def _apply(project: str, record_id: str, previous: str, target: str | None, observed: Inspection):
    config, path, original = observed.config, observed.path, observed.original
    data, proof = observed.data, observed.proof
    fixed = _replacement(original.decode("utf-8"), data, config.machine)
    digest = hashlib.sha256(original).hexdigest()
    audit = path.with_name(f"{path.name}.machine-label-{digest[:16]}.json")
    with pr_authority.guard(timeout=0):
        # Git/config proof precedes the short record lock; publication authority
        # keeps receiving registration stable until the exact-byte commit.
        fresh = _inspect(project, record_id, previous, target)
        if (
            fresh.path != path or fresh.original != original or fresh.proof != proof
            or _binding(fresh.config) != _binding(config)
        ):
            raise ValueError("record or receiving proof changed; inspect again before repair")
        receipt = {
            "version": 1, "scope": "machine-record", "state": "prepared",
            "project": project, "worktree_id": record_id,
            "from_machine": previous, "to_machine": config.machine, "platform": config.platform,
            "proof": proof, "checkout_path": data["worktree_path"],
            "anchor": config.default_repo.anchor, "worktree_root": config.default_repo.worktree_root,
            "before_sha256": digest, "after_sha256": hashlib.sha256(fixed.encode("utf-8")).hexdigest(),
            "references": "preserved", "backup": None,
        }
        with tracking._RecordLock(path, blocking=False) as lock:
            if not lock.acquired:
                raise TimeoutError("tracking record is busy; machine-label repair refused")
            if path.is_symlink() or path.read_bytes() != original:
                raise ValueError("record changed after proof; inspect again before repair")
            if audit.exists() or audit.is_symlink():
                raise ValueError(f"prior machine-label audit exists; inspect before retrying: {audit}")
            tracking._atomic_write(audit, json.dumps(receipt, indent=2) + "\n")
            backup = tracking_yaml_repair.apply_repair(path, original, fixed)
            receipt["backup"] = backup.name
            receipt["state"] = "committed"
            try:
                tracking._atomic_write(audit, json.dumps(receipt, indent=2) + "\n")
            except OSError as exc:
                raise RepairAuditError(backup, audit) from exc
    return backup, audit


def _replacement(raw: str, data: dict, target: str) -> str:
    document = yaml.compose(raw)
    if not isinstance(document, yaml.MappingNode):
        raise ValueError("record must be a YAML mapping")
    values = [
        value for key, value in document.value
        if isinstance(key, yaml.ScalarNode) and key.value == "machine"
    ]
    if len(values) != 1:
        raise ValueError("record needs exactly one top-level machine scalar")
    value = values[0]
    if (
        not isinstance(value, yaml.ScalarNode) or value.value != data["machine"]
        or value.start_mark.line != value.end_mark.line
    ):
        raise ValueError("machine label is not a standalone scalar")
    fixed = raw[:value.start_mark.index] + json.dumps(target) + raw[value.end_mark.index:]
    if tracking_yaml_repair.strict_mapping(fixed) != {**data, "machine": target}:
        raise ValueError("repair would change fields outside the machine label")
    return fixed


def targeted_doctor(args) -> int:
    record_id = getattr(args, "repair_machine_record", None)
    previous = getattr(args, "from_machine", None)
    target = getattr(args, "to_machine", None)
    apply = getattr(args, "fix", False)
    report = {
        "version": 1, "scope": "machine-record", "worktree_id": record_id,
        "mode": "fix" if apply else "report", "repairable": False,
        "repaired": False, "error": None, "resident_projection": "not-verified",
    }
    try:
        if (
            not isinstance(record_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", record_id)
            or ".." in record_id
            or not isinstance(previous, str) or not previous.strip()
        ):
            raise ValueError("repair requires an exact worktree ID and --from-machine")
        if any(getattr(args, field, None) for field in (
            "guest_distribution", "repair_creation", "seed_id", "repair_record",
            "bound_agent_end_field", "gc_sessions", "prune_pivots", "apply_daemon_health",
        )):
            raise ValueError("machine-record repair cannot be combined with other doctor passes")
        project = cfg.project_name()
        if not project:
            raise ValueError("machine-record repair requires an explicitly selected project")
        observed = _inspect(project, record_id, previous, target)
        config, original, data, proof = observed.config, observed.original, observed.data, observed.proof
        report.update(
            project=project, from_machine=previous, to_machine=config.machine,
            proof=proof, references="preserved",
            before_sha256=hashlib.sha256(original).hexdigest(),
        )
        if data["machine"] != config.machine:
            _replacement(original.decode("utf-8"), data, config.machine)
            report["repairable"] = True
            if apply:
                backup, audit = _apply(project, record_id, previous, target, observed)
                report.update(backup=backup.name, audit=audit.name)
                report["repaired"] = True
    except RepairAuditError as exc:
        report.update(repaired=True, backup=exc.backup.name, audit=exc.audit.name, error=str(exc))
    except (
        OSError, ValueError, TypeError, RuntimeError, yaml.YAMLError, git_ops.GitError,
        subprocess.SubprocessError,
    ) as exc:
        report["error"] = str(exc)
    if getattr(args, "json", False):
        print(json.dumps(report))
    elif report["error"]:
        output.err(report["error"])
    else:
        output.ok(f"Record {record_id} machine: " + (
            "repaired" if report["repaired"] else "repairable" if report["repairable"] else "current"
        ))
    return 1 if report["error"] else 0
