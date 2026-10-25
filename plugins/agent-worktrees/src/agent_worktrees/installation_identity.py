"""Local naming admission shared by project registration and installation."""

from __future__ import annotations

from pathlib import Path

from machine_transport import resolve_local_execution_context

from . import config as cfg
from .machine_identity import configured_machine_binding, resolve_execution_machine
from .project_adoption import existing_project_state


def _retained_binding(
    project: str, repo_dir: Path, configured_machine: str | None, *, force: bool,
) -> str | None:
    global_path = cfg.global_config_path()
    global_raw = cfg._load_yaml_safe(global_path)
    if not global_path.exists() and configured_machine is not None:
        global_raw["machine"] = configured_machine
    state = existing_project_state(project, repo_dir)
    if state is None:
        return configured_machine_binding({}, global_raw, {})
    path = state / "config.yaml"
    project_raw = {} if force else cfg._load_yaml_safe(path)
    dropins = cfg._load_config_d(path.parent / "config.d", project_name=project, warn=True)
    return configured_machine_binding(cfg._deep_merge(dropins, project_raw), global_raw, {})


def local_registration_identity(
    repo_dir: Path, configured_machine: str | None, project: str, *, force: bool,
) -> tuple[str, str, cfg.MachineEntry | None]:
    """Validate optional customization before any project or runtime writes."""
    try:
        entries = cfg.load_machines_yaml(repo_dir)
    except FileNotFoundError:
        entries = {}
    platform = cfg.detect_platform()
    binding = _retained_binding(project, repo_dir, configured_machine, force=force)
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
