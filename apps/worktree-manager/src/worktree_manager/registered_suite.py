"""Reconcile one prepared suite selection with all registered harness demands."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
import json
import os
from pathlib import Path
import stat
from typing import Literal

from . import application_footprints, harness_state, repository_suite_roster, runtime_source_repository, source_config


@dataclass(frozen=True)
class PreparedSuite:
    commit: str
    marketplace: Path
    footprint: Path


@dataclass(frozen=True)
class HarnessPolicy:
    gate_paths: tuple[Path, ...]
    machines: tuple[str, ...]
    index_role: Literal["host", "client", "unconfigured"] | None = None
    vault_state: Literal["configured", "configuration-empty"] | None = None
    fleet_config: Path | None = None
    fleet_target: str | None = None


def fleet_service_roles(path: Path, target: str) -> tuple[str, ...]:
    if not path.is_absolute() or not target:
        raise ValueError("Fleet policy requires an addressed configuration and target machine")
    runtime_source_repository._ordinary(path)
    try:
        owner = import_module("worktree_manager._runtime_assets.scripts.runtime-source-tools.fleet_role_policy.target_policy")
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Fleet policy must be a regular file")
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
                raise ValueError("Fleet policy changed during observation")
            raw = stream.read(owner.MAX_BYTES + 1)
        result = owner.service_roles(raw, target)
    except (ImportError, OSError, ValueError) as error:
        raise ValueError("Fleet target policy could not be resolved") from error
    if not isinstance(result, tuple) or not all(isinstance(role, str) for role in result):
        raise ValueError("Invalid Fleet target policy observation")
    return result


def _requires_fleet(policy: HarnessPolicy) -> bool:
    if policy.fleet_config is None and policy.fleet_target is None:
        return False
    if policy.fleet_config is None or not policy.fleet_target or policy.fleet_target not in policy.machines:
        raise ValueError("Fleet policy requires an addressed configuration and target machine")
    return bool(fleet_service_roles(policy.fleet_config, policy.fleet_target))


def registrations(root: Path) -> tuple[str, dict[str, Path]]:
    registry = root / "registry"
    if not root.is_absolute():
        raise ValueError("An absolute suite footprint is required")
    for name in ("footprint.json", "projects.yaml", "repos.yaml"):
        runtime_source_repository._ordinary(registry / name)
        if not (registry / name).is_file():
            raise ValueError(f"Missing footprint registry input: {name}")
    receipt = json.loads((registry / "footprint.json").read_bytes())
    if (receipt.get("schema"), receipt.get("version"), receipt.get("root")) != (
        "copilot-extensions.suite-footprint", 1, str(root),
    ):
        raise ValueError("Footprint identity mismatch")
    inputs = harness_state.ProfileInputRoots(registry, {})
    projects = harness_state.projects_registry(input_roots=inputs, strict=True)["projects"]
    repos = harness_state.repos_registry(input_roots=inputs, strict=True)
    docs = {registry / "projects.yaml": {"projects": projects}, registry / "repos.yaml": repos}
    default = receipt["default_project"]
    if not isinstance(default, str) or default not in projects:
        raise ValueError("Default harness is not registered")
    anchors = {}
    for name in sorted(projects):
        selected = harness_state.project_anchor_selection(name, input_roots=inputs, reader=docs.__getitem__)
        if not selected.anchor or not Path(selected.anchor).is_absolute() or not Path(selected.anchor).is_dir():
            raise ValueError(f"Registered harness {name} has no available anchor")
        anchor = Path(selected.anchor)
        if (anchor / "HEAD").is_file() and (anchor / "objects").is_dir():
            raise ValueError(f"Registered harness {name} requires a materialized worktree, not a Git directory")
        anchors[name] = anchor
    return default, anchors


def registered_plan(
    root: Path, *, resolve: Callable[[str, str], PreparedSuite],
    observe: Callable[[str, Path], HarnessPolicy],
) -> tuple[application_footprints.Application, ...]:
    """Source acquisition and harness-specific policy observations belong to the caller."""
    default, anchors = registrations(root)
    selection = runtime_source_repository.read(anchors[default], default_repository=source_config.DEFAULT_REPO)
    suite = resolve(selection["repository"], selection["reference"])
    wanted: dict[str, list[str]] = {}
    for name, anchor in anchors.items():
        policy = observe(name, anchor)
        try:
            result = repository_suite_roster.plan(
                anchor, suite.marketplace, gate_paths=policy.gate_paths, machines=policy.machines,
                index_role=policy.index_role, vault_state=policy.vault_state,
            )
            fleet = _requires_fleet(policy)
        except (ValueError, OSError) as exc:
            raise ValueError(f"Harness {name}, selected commit {suite.commit}: {exc}") from exc
        if not result["complete"]:
            raise ValueError(f"Harness {name} has unresolved requirements: {result['unresolved']}")
        required = result["required"]
        if not isinstance(required, list):
            raise ValueError(f"Invalid application roster for {name}")
        for app in required:
            wanted.setdefault(app, []).append(name)
        if fleet:
            wanted.setdefault("fleet-manager", []).append(name)
    apps_root = root / "apps"
    inventory = {app.name for app in application_footprints.plan(suite.footprint, apps_root)}
    for app, harnesses in wanted.items():
        if app not in inventory:
            raise ValueError(f"Harness {', '.join(harnesses)} requires {app}, absent from selected commit {suite.commit}")
    return application_footprints.plan(suite.footprint, apps_root, selected=tuple(sorted(wanted)))
