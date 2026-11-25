"""Native identity acquisition without launch or publication policy reads."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from .config_acquisition import StrictOptions

if TYPE_CHECKING:
    from .config import Config


def registered_project_for_anchor(anchor: Path, *, absolute_only: bool = False) -> str | None:
    """Resolve membership through the native readers, without CLI imports."""
    from . import config as cfg, git_ops, repos, registry_paths
    from .projects_registry import project_for_anchor, read_projects_registry

    return project_for_anchor(
        anchor,
        read_projects=lambda: read_projects_registry(
            registry_paths.registry_path("projects.yaml", legacy_root=cfg.install_dir()),
        ),
        read_repos=repos.read_registry,
        platform=cfg.detect_platform,
        normalize=git_ops._normalize_wt_path,
        absolute_only=absolute_only,
    )


def machine_layers(
    path: Path, *, project: str | None = None, strict: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read shared global, project and drop-in layers through their native owners."""
    from . import config as cfg

    strict_sources: StrictOptions = {"strict": True} if strict else {}
    global_raw = cfg.config_migrations.migrate_loaded(
        cfg._load_yaml_safe(cfg.global_config_path(), **strict_sources),
        cfg.config_migrations.SCHEMA_CONFIG,
    )
    machine_raw = cfg._load_yaml_safe(path, **strict_sources)
    repo_name = (
        project or machine_raw.get("repo_name") or global_raw.get("repo_name")
        or cfg._project_name_safe()
    )
    dropins = cfg._load_config_d(
        path.parent / "config.d", project_name=str(repo_name or ""), warn=True,
        **strict_sources,
    )
    if dropins:
        machine_raw = cfg._deep_merge(dropins, machine_raw)
    return global_raw, machine_raw


def load_identity(
    path: Path | None = None, *, project: str | None = None, absolute_only: bool = False,
) -> Config:
    """Acquire identity data without constructing launch or publication settings."""
    from . import config as cfg, config_acquisition
    from .config_sources import knowledge_binding_value
    from .machine_identity import configured_machine_binding

    path = path if path is not None else cfg.default_config_path()
    global_raw, machine_raw = cfg._machine_config_layers(path, project=project)
    repo_name = (
        project or machine_raw.get("repo_name") or global_raw.get("repo_name")
        or cfg._project_name_safe()
    )
    platform = machine_raw.get("platform") or global_raw.get("platform") or cfg.detect_platform()
    machine_repos = machine_raw.get("repos") or {}
    selected = machine_repos.get(repo_name) if isinstance(machine_repos, dict) else {}
    selected = selected if isinstance(selected, dict) else {}
    anchor = selected.get("anchor") or cfg._resolve_anchor_from_registry(repo_name, platform)
    if not anchor:
        raise cfg.ProjectNotRegisteredError(f"No repo could be resolved for project {repo_name!r}")
    configured = configured_machine_binding(machine_raw, global_raw)
    knowledge_repo = knowledge_binding_value(machine_raw, global_raw)
    if absolute_only:
        if not Path(anchor).is_absolute():
            raise ValueError("resident configuration requires an absolute anchor")
        if knowledge_repo:
            from .state_root import _checkout_path

            knowledge = _checkout_path(knowledge_repo)
            if knowledge is not None and not Path(knowledge).is_absolute():
                raise ValueError("resident configuration requires an absolute knowledge root")
    identity = cfg.Config(
        srcroot=machine_raw.get("srcroot") or global_raw.get("srcroot") or "",
        repo_name=repo_name,
        platform=platform,
        machine=configured or cfg.detect_machine(),
        configured_machine=configured or None,
        knowledge_repo=knowledge_repo,
        repos={repo_name: cfg.RepoConfig(
            anchor=anchor, worktree_root=str(cfg.derive_worktree_root(anchor)),
        )},
    )

    def source_config() -> Config:
        settings = cfg._deep_merge(cfg._load_inrepo_config(anchor), selected)
        return replace(identity, repos={repo_name: replace(
            identity.default_repo,
            stateless=bool(settings.get("stateless", False)),
            requires_external_state_root=bool(settings.get("requires_external_state_root", False)),
        )})

    return config_acquisition.owner_call(
        config_acquisition.execution_config, config_acquisition.EXECUTION_CONFIG_OWNER,
        identity, configured, machine_raw, global_raw, source="execution-machine",
        source_config=source_config,
    )
