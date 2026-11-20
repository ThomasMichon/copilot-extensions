"""Plan inactive application footprints from one worktree and one suite source."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Literal

from dropin_registry import ScanAuthority
from plugin_activation._config_io import _read_settings
from plugin_activation.state import read_json_object, validate_identity
from plugin_resolve import SETTINGS_RELS, load_marketplace, plugin_dir, split_source
from repository_policy.runtime_gate import allows_runtime, parse_gate_manifest
import yaml

from .runtime_source_repository import _ordinary


def _directory(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("Suite planning requires an explicit absolute directory")
    _ordinary(path)
    if not path.is_dir():
        raise ValueError(f"Suite planning directory is unavailable: {path}")
    return path


def _gate(paths: tuple[Path, ...]) -> tuple[bool, dict[str, set[str]]]:
    present = False
    gate: dict[str, set[str]] = {}
    for path in paths:
        if not path.is_absolute():
            raise ValueError("Runtime policy paths must be absolute")
        _ordinary(path)
        try:
            with path.open(encoding="utf-8") as stream:
                data = yaml.safe_load(stream)
        except FileNotFoundError:
            continue
        except yaml.YAMLError:
            raise ValueError("Invalid runtime deployment manifest") from None
        present = True
        if data is not None and not isinstance(data, dict):
            raise ValueError("Runtime deployment manifest must be a mapping")
        if data is not None:
            groups = [data["plugins"]] if "plugins" in data else []
            if "repos" in data:
                if not isinstance(data["repos"], dict):
                    raise ValueError("Invalid runtime policy repositories")
                for repo in data["repos"].values():
                    if not isinstance(repo, dict):
                        raise ValueError("Invalid runtime policy repository")
                    if "services" in repo:
                        groups.append(repo["services"])
            for entries in groups:
                if not isinstance(entries, list):
                    raise ValueError("Invalid runtime policy selector list")
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise ValueError("Invalid runtime policy selector")
                    if "deploy_machines" in entry and (
                        not isinstance(entry.get("name"), str) or not entry["name"].strip()
                        or not isinstance(entry["deploy_machines"], list)
                        or not all(isinstance(m, str) and m.strip() for m in entry["deploy_machines"])
                    ):
                        raise ValueError("Invalid runtime policy machine selector")
        parse_gate_manifest(data, gate)
        if gate:
            break
    return present, gate


def plan(
    worktree: Path, source: Path, *, gate_paths: tuple[Path, ...],
    machines: tuple[str, ...], index_role: Literal["host", "client", "unconfigured"] | None = None,
    vault_state: Literal["configured", "configuration-empty"] | None = None,
) -> dict[str, object]:
    """Consume composed settings and ordered gate inputs; never consult installed inventory."""
    if index_role not in (None, "host", "client", "unconfigured") or vault_state not in (
        None, "configured", "configuration-empty",
    ):
        raise ValueError("Invalid app policy observation")
    target = _directory(worktree)
    for parts in SETTINGS_RELS:
        entry = target
        for part in parts:
            entry = entry / part
            try:
                entry.lstat()
            except FileNotFoundError:
                break
            try:
                entry.stat()
            except FileNotFoundError as error:
                raise ValueError(f"Indeterminate plugin settings: dangling link at {entry}") from error
    settings = _read_settings(target, SETTINGS_RELS)
    if settings.authority is ScanAuthority.INDETERMINATE:
        raise ValueError("Effective worktree plugin settings are indeterminate")
    market = load_marketplace(_directory(source))
    if market is None or market.name != "copilot-extensions":
        raise ValueError("Selected folder has no copilot-extensions marketplace")
    present, gate = _gate(gate_paths)
    rows: dict[str, dict[str, object]] = {}
    required = ["worktree-manager"]
    unresolved = []
    for identity in sorted(settings.settings.enabled_sources()):
        validate_identity(identity)
        name, marketplace = split_source(identity)
        if marketplace != market.name:
            continue
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise ValueError("Invalid suite plugin identity")
        payload = plugin_dir(market, name)
        if payload is None:
            raise ValueError(f"Enabled plugin absent or ambiguous in selected source: {name}")
        _, manifest = read_json_object(payload / "plugin.json")
        if manifest.get("name") != name:
            raise ValueError(f"Selected source has no matching plugin manifest: {name}")
        scope = manifest.get("runtimeScope", "none")
        if scope not in ("universal", "machine-gated", "none"):
            raise ValueError(f"Invalid runtime scope: {name}")
        if scope == "none":
            state = "payload-only"
        elif present and scope == "machine-gated" and gate.get(name) and not machines:
            state = "needs-machine-identity"
        elif not allows_runtime(scope, name, machines, gate, present=present):
            state = "machine-excluded"
        elif (name == "agent-index" and index_role is None) or (name == "agent-vault" and vault_state is None):
            state = "needs-app-policy"
        elif (name == "agent-index" and index_role == "unconfigured") or (
            name == "agent-vault" and vault_state == "configuration-empty"
        ):
            state = "not-configured"
        else:
            state = "required"
            required.append(name)
        if state.startswith("needs-"):
            unresolved.append(name)
        rows[name] = {"scope": scope, "state": state}
    return {
        "schema": "worktree-manager.repository-suite-roster", "version": 1,
        "complete": not unresolved, "plugins": rows, "required": required,
        "unresolved": unresolved, "index_role": index_role, "vault_state": vault_state,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    policy = parser.add_mutually_exclusive_group(required=True)
    policy.add_argument("--gate-path", type=Path, action="append", help="ordered policy candidate (repeatable)")
    policy.add_argument("--no-gate", action="store_true", help="the repository owner resolved no gate")
    parser.add_argument("--machine", action="append", help="qualified physical/execution name")
    parser.add_argument("--index-role", choices=("host", "client", "unconfigured"))
    parser.add_argument("--vault-state", choices=("configured", "configuration-empty"))
    args = parser.parse_args(argv)
    try:
        result = plan(
            args.worktree, args.source, gate_paths=tuple(args.gate_path or ()),
            machines=tuple(args.machine or ()), index_role=args.index_role, vault_state=args.vault_state,
        )
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
