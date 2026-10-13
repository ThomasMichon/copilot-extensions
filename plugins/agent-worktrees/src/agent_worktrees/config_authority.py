"""Bounded receiving-side configuration for execution-space authority checks."""

from __future__ import annotations

from pathlib import Path

_INREPO_AUTHORITY_BUDGET_S = 1.0


def load_project_authority_config(
    name: str, *, path: Path | None = None,
):
    """Resolve only identity, registry, and state-root facts used by fences."""
    from . import config as cfg

    path = path or cfg.project_dir(name) / "config.yaml"
    global_raw = cfg.config_migrations.migrate_loaded(
        cfg._load_yaml_safe(cfg.global_config_path()),
        cfg.config_migrations.SCHEMA_CONFIG,
    )
    machine_raw = cfg._load_yaml_safe(path)
    dropins = cfg._load_config_d(
        path.parent / "config.d",
        project_name=name,
        warn=True,
    )
    if dropins:
        machine_raw = cfg._deep_merge(dropins, machine_raw)

    platform_name = (
        machine_raw.get("platform")
        or global_raw.get("platform")
        or cfg.detect_platform()
    )
    machine = (
        machine_raw.get("machine")
        or global_raw.get("machine")
        or cfg.detect_machine()
    )
    machine_repos = machine_raw.get("repos") or {}
    machine_repo = (
        machine_repos.get(name) or {}
        if isinstance(machine_repos, dict)
        else {}
    )
    if not isinstance(machine_repo, dict):
        machine_repo = {}
    anchor = machine_repo.get("anchor") or cfg._resolve_anchor_from_registry(
        name, platform_name
    )
    if not anchor:
        raise ValueError(
            f"No repo could be resolved for project {name!r} from "
            f"{path} or the repos registry"
        )

    with cfg.inrepo_config_source.resolution_budget(
        _INREPO_AUTHORITY_BUDGET_S, clamp=True,
    ):
        inrepo_authority = cfg._load_inrepo_config(str(anchor))
    stateless = bool(
        machine_repo.get("stateless", inrepo_authority.get("stateless", False))
    )
    requires_external_state_root = bool(
        machine_repo.get(
            "requires_external_state_root",
            inrepo_authority.get("requires_external_state_root", False),
        )
        or stateless
    )
    worktree_root = machine_repo.get("worktree_root") or cfg.derive_worktree_root(
        anchor
    )
    config = cfg.Config(
        srcroot=machine_raw.get("srcroot") or global_raw.get("srcroot") or "",
        machine=machine,
        platform=platform_name,
        repo_name=name,
        knowledge_repo=(
            machine_raw.get("knowledge_repo")
            or global_raw.get("knowledge_repo")
            or ""
        ),
        repos={
            name: cfg.RepoConfig(
                anchor=str(anchor),
                worktree_root=str(worktree_root),
                stateless=stateless,
                requires_external_state_root=requires_external_state_root,
            ),
        },
    )
    from .execution_spaces import canonicalize_legacy_config

    return canonicalize_legacy_config(config)
