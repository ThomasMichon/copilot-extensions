"""Reconcile one prepared suite selection with all registered harness demands."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
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
        except (ValueError, OSError) as exc:
            raise ValueError(f"Harness {name}, selected commit {suite.commit}: {exc}") from exc
        if not result["complete"]:
            raise ValueError(f"Harness {name} has unresolved requirements: {result['unresolved']}")
        required = result["required"]
        if not isinstance(required, list):
            raise ValueError(f"Invalid application roster for {name}")
        for app in required:
            wanted.setdefault(app, []).append(name)
    apps_root = root / "apps"
    inventory = {app.name for app in application_footprints.plan(suite.footprint, apps_root)}
    for app, harnesses in wanted.items():
        if app not in inventory:
            raise ValueError(f"Harness {', '.join(harnesses)} requires {app}, absent from selected commit {suite.commit}")
    return application_footprints.plan(suite.footprint, apps_root, selected=tuple(sorted(wanted)))
