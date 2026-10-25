"""Local naming admission shared by project registration and installation."""

from __future__ import annotations

from pathlib import Path

from machine_transport import resolve_local_execution_context

from . import config as cfg
from .config_sources import knowledge_config_source
from .machine_identity import configured_machine_binding, resolve_execution_machine
from .machine_config import config_overlay_root, machine_config_root
from .project_adoption import existing_project_state


def _prospective_configs(
    project: str, repo_dir: Path, configured_machine: str | None, *, force: bool,
) -> tuple[cfg.Config, cfg.Config, Path | None]:
    global_path = cfg.global_config_path()
    global_raw = cfg._load_yaml_safe(global_path)
    if not global_path.exists() and configured_machine is not None:
        global_raw["machine"] = configured_machine
    state = existing_project_state(project, repo_dir)
    path = state / "config.yaml" if state is not None else None
    project_raw = (
        cfg._load_yaml_safe(path) if path is not None and path.exists() and not force
        else {"repo_name": project, "repos": {project: {"anchor": str(repo_dir)}}}
    )
    dropins = (
        cfg._load_config_d(state / "config.d", project_name=project, warn=True)
        if state is not None else {}
    )
    machine_raw = cfg._deep_merge(dropins, project_raw)
    preliminary = project_raw.get("repo_name") or global_raw.get("repo_name") or cfg._project_name_safe() or project
    ordinary_raw = cfg._deep_merge(
        cfg._load_config_d(state / "config.d", project_name=preliminary, warn=True)
        if state is not None and preliminary != project else dropins,
        project_raw,
    )

    def for_project(name: str, machine_raw: dict) -> cfg.Config:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("repo_name must be a repository name")
        binding = configured_machine_binding(machine_raw, global_raw, {})
        machine_repos = machine_raw.get("repos")
        if not isinstance(machine_repos, dict):
            machine_repos = {}
        knowledge_repo = machine_raw.get("knowledge_repo") or global_raw.get("knowledge_repo") or ""
        if not isinstance(knowledge_repo, str):
            raise ValueError("knowledge_repo must be a repository name")
        platform = machine_raw.get("platform") or global_raw.get("platform") or cfg.detect_platform()
        names = [key for key, value in machine_repos.items() if isinstance(value, dict)]
        if name not in names:
            names.append(name)
        repos = {}
        for key in names:
            machine_repo = machine_repos.get(key) or {}
            if not isinstance(machine_repo, dict):
                machine_repo = {}
            anchor = machine_repo.get("anchor") or (
                str(repo_dir) if key == project and platform == cfg.detect_platform()
                else cfg._resolve_anchor_from_registry(key, platform)
            )
            if not anchor:
                continue
            settings = cfg._deep_merge(cfg._load_inrepo_config(anchor), machine_repo)
            repos[key] = cfg._build_repo_config(settings, anchor, cfg.derive_worktree_root(anchor))
        config = cfg.Config(
            srcroot=machine_raw.get("srcroot") or global_raw.get("srcroot") or "",
            machine=binding or "", configured_machine=binding,
            platform=platform, repo_name=name, repos=repos, knowledge_repo=knowledge_repo,
        )
        try:
            config.default_repo
        except KeyError as error:
            raise ValueError(f"Prospective project configuration has no default repository: {name}") from error
        return config

    ordinary = ordinary_raw.get("repo_name") or global_raw.get("repo_name") or cfg._project_name_safe() or project
    return for_project(project, machine_raw), for_project(ordinary, ordinary_raw), state


def _registry_root(config: cfg.Config, state: Path | None) -> Path | None:
    anchor = Path(config.default_repo.anchor)
    overlay = config_overlay_root(str(state), str(anchor)) if state is not None else None
    roots = [anchor]
    if overlay:
        roots.append(Path(overlay))
    if knowledge := knowledge_config_source(config, base_anchor=str(anchor)):
        roots.append(Path(knowledge.anchor))
    for root in roots:
        if any((root / relative).exists() for relative in (
            Path(cfg.INREPO_CONFIG_DIRNAME) / "machines.yaml", Path("machines.yaml"),
        )):
            return root
    return None


def _admit(
    prospective: cfg.Config, state: Path | None, configured_machine: str | None,
) -> tuple[str, str, cfg.MachineEntry | None]:
    root = _registry_root(prospective, state)
    try:
        entries = cfg.load_machines_yaml(root, config=prospective) if root is not None else {}
    except FileNotFoundError:
        entries = {}
    platform = cfg.detect_platform()
    binding = prospective.configured_machine
    if configured_machine is not None and configured_machine != binding:
        resolve_local_execution_context(
            entries, hostname=cfg.socket.gethostname(), environment=platform,
            configured_machine=configured_machine,
        )
    context = resolve_local_execution_context(
        entries, hostname=cfg.socket.gethostname(), environment=platform,
        configured_machine=binding,
    )
    machine = binding or resolve_execution_machine(
        context.hostname, guest=platform == "wsl", configured=False,
        load_entries=lambda: entries,
    )
    entry = entries.get(context.customization_key) if context.customization_key else None
    return machine, platform, entry


def local_registration_identity(
    repo_dir: Path, configured_machine: str | None, project: str, *, force: bool,
) -> tuple[str, str, cfg.MachineEntry | None]:
    """Validate explicit-project and ordinary loads before any state writes."""
    prospective, ordinary, state = _prospective_configs(
        project, repo_dir, configured_machine, force=force,
    )
    result = _admit(prospective, state, configured_machine)
    if ordinary != prospective:
        root = machine_config_root(ordinary.default_repo.anchor, project=ordinary.repo_name)
        _admit(ordinary, Path(root) if root else None, configured_machine)
    return result
