"""Explicit same-native-machine label repair, without moving worktree identity."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from pathlib import Path

import yaml
from machine_transport import resolve_machine_selection

from . import config as cfg, execution_spaces, git_ops, output, tracking_yaml_repair


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
    apply = getattr(args, "fix", False)
    report = {
        "version": 1, "scope": "machine-record", "worktree_id": record_id,
        "mode": "fix" if apply else "report", "repairable": False,
        "repaired": False, "error": None,
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
        config = cfg.load_config()
        if config.repo_name != project:
            raise ValueError("resolved project does not match the selected project")
        _prove_local_alias(config, previous)
        report.update(project=project, from_machine=previous, to_machine=config.machine)
        path = cfg.tracking_dir() / f"{record_id}.yaml"
        if path.is_symlink() or path.resolve() != path.absolute():
            raise ValueError("tracking record path indirection refused")
        observed = path.read_bytes()
        raw = observed.decode("utf-8")
        data = tracking_yaml_repair.strict_mapping(raw)
        if data.get("worktree_id") != record_id:
            raise ValueError("record identity does not match the selected worktree")
        if data.get("machine") not in (previous, config.machine):
            raise ValueError("record machine does not match --from-machine or the local target")
        _prove_checkout(data, config)
        if data["machine"] != config.machine:
            fixed = _replacement(raw, data, config.machine)
            report["repairable"] = True
            if apply:
                report["backup"] = tracking_yaml_repair.apply_repair(path, observed, fixed).name
                report["repaired"] = True
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
