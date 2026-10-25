"""Local naming admission shared by project registration and installation."""

from __future__ import annotations

from pathlib import Path

from machine_transport import resolve_local_execution_context

from . import config as cfg
from .machine_identity import configured_machine_binding, resolve_execution_machine
from .machine_config import config_overlay_root
from .project_adoption import existing_project_state


def _prospective_config(
    project: str, repo_dir: Path, configured_machine: str | None, *, force: bool,
) -> tuple[cfg.Config, Path | None]:
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
    binding = configured_machine_binding(machine_raw, global_raw, {})
    machine_repos = machine_raw.get("repos")
    machine_repo = machine_repos.get(project) if isinstance(machine_repos, dict) else None
    if not isinstance(machine_repo, dict):
        machine_repo = {}
    anchor = machine_repo.get("anchor") or str(repo_dir)
    settings = cfg._deep_merge(cfg._load_inrepo_config(anchor), machine_repo)
    repo = cfg._build_repo_config(settings, anchor, cfg.derive_worktree_root(anchor))
    knowledge_repo = machine_raw.get("knowledge_repo") or global_raw.get("knowledge_repo") or ""
    if not isinstance(knowledge_repo, str):
        raise ValueError("knowledge_repo must be a repository name")
    return cfg.Config(
        srcroot=machine_raw.get("srcroot") or global_raw.get("srcroot") or "",
        machine=binding or "", configured_machine=binding,
        platform=cfg.detect_platform(), repo_name=project, repos={project: repo},
        knowledge_repo=knowledge_repo,
    ), state


def _registry_root(config: cfg.Config, state: Path | None) -> Path:
    anchor = Path(config.default_repo.anchor)
    overlay = config_overlay_root(str(state), str(anchor)) if state is not None else None
    for root in (anchor, Path(overlay) if overlay else anchor):
        if any((root / relative).is_file() for relative in (
            Path(cfg.INREPO_CONFIG_DIRNAME) / "machines.yaml", Path("machines.yaml"),
        )):
            return root
    return anchor


def local_registration_identity(
    repo_dir: Path, configured_machine: str | None, project: str, *, force: bool,
) -> tuple[str, str, cfg.MachineEntry | None]:
    """Validate optional customization before any project or runtime writes."""
    prospective, state = _prospective_config(project, repo_dir, configured_machine, force=force)
    try:
        entries = cfg.load_machines_yaml(_registry_root(prospective, state), config=prospective)
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
