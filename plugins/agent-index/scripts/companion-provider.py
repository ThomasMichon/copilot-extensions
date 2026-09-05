#!/usr/bin/env python3
"""Resolve repository-scoped agent-index companion activation."""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from resolve_effective_config import _load_yaml_mapping, resolve
from companion_context import installation_mode


def _platform_key() -> str:
    if platform.system() == "Windows":
        return "windows"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    return "linux"


def _project_roots(scopes: list[str]) -> list[Path]:
    projects = sorted(
        {
            scope.removeprefix("project:")
            for scope in scopes
            if scope.startswith("project:") and scope != "project:"
        }
    )
    if not projects:
        return []

    state, document = _load_yaml_mapping(Path.home() / ".agent-worktrees" / "repos.yaml")
    if state != "ready" or document is None:
        raise RuntimeError("agent-worktrees repository registry is unavailable")
    repos = document.get("repos")
    if not isinstance(repos, dict):
        raise RuntimeError("agent-worktrees repository registry is malformed")

    key = _platform_key()
    roots: list[Path] = []
    for project in projects:
        entry = repos.get(project)
        root = entry.get(key) if isinstance(entry, dict) else None
        if not isinstance(root, str) or not root.strip():
            raise RuntimeError(f"activation scope {project!r} has no {key} checkout")
        roots.append(Path(root).expanduser())
    return roots


def _request() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError("unsupported companion request")
    scopes = value.get("activation_scopes")
    machine = value.get("machine")
    if (
        not isinstance(scopes, list)
        or not all(isinstance(scope, str) and scope for scope in scopes)
        or not isinstance(machine, str)
        or not machine.strip()
    ):
        raise RuntimeError("companion request is incomplete")
    return value


def _supports_companion_mode(environment: dict[str, str]) -> bool:
    return installation_mode()["supported"]


def _active_environment(request: dict[str, Any]) -> dict[str, str] | None:
    machine = request["machine"].strip().casefold()
    roots = _project_roots(request["activation_scopes"])
    active: dict[tuple[str, str], dict[str, str]] = {}

    for root in roots:
        result = resolve(root)
        if not result.get("opted_in"):
            reason = result.get("reason")
            if isinstance(reason, str) and (
                reason.endswith("-unavailable")
                or reason
                in {
                    "repository-unavailable",
                    "repository-override-unavailable",
                    "external-state-root-invalid",
                }
            ):
                raise RuntimeError(f"effective agent-index configuration is uncertain: {reason}")
            continue
        indexers = result.get("indexers")
        if not isinstance(indexers, list):
            raise RuntimeError("effective agent-index configuration is malformed")
        host = any(
            isinstance(indexer, dict)
            and isinstance(indexer.get("machine"), str)
            and indexer["machine"].strip().casefold() == machine
            for indexer in indexers
        )
        if not host:
            continue
        config = result.get("config")
        repo_root = result.get("repo_root")
        if not isinstance(config, str) or not isinstance(repo_root, str):
            raise RuntimeError("host activation lacks attributable configuration")
        environment = {
            "AGENT_INDEX_EFFECTIVE_CONFIG": config,
            "AGENT_INDEX_MACHINE": request["machine"].strip(),
            "AGENT_INDEX_NO_SELFPROVISION": "1",
            "AGENT_INDEX_REPO": repo_root,
        }
        active[(config, repo_root)] = environment

    if not active:
        return None
    if len(active) != 1:
        raise RuntimeError("multiple agent-index host configurations are active on this machine")
    environment = next(iter(active.values()))
    return environment if _supports_companion_mode(environment) else None


def main() -> int:
    try:
        request = _request()
        for name in (
            "AGENT_INDEX_CONFIG_DATA_B64",
            "AGENT_INDEX_EFFECTIVE_CONFIG",
            "AGENT_INDEX_FORWARDED",
            "AGENT_INDEX_REPO",
        ):
            os.environ.pop(name, None)
        environment = _active_environment(request)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"agent-index companion activation is indeterminate: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "schema_version": 1,
                "active": environment is not None,
                **({"environment": environment} if environment is not None else {}),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
