"""Local naming admission shared by project registration and installation."""

from __future__ import annotations

from pathlib import Path

from machine_transport import resolve_local_execution_context

from . import config as cfg
from .machine_identity import resolve_execution_machine


def local_registration_identity(
    repo_dir: Path, configured_machine: str | None,
) -> tuple[str, str, cfg.MachineEntry | None]:
    """Validate optional customization before any project or runtime writes."""
    try:
        entries = cfg.load_machines_yaml(repo_dir)
    except FileNotFoundError:
        entries = {}
    platform = cfg.detect_platform()
    context = resolve_local_execution_context(
        entries, hostname=cfg.socket.gethostname(), environment=platform,
        configured_machine=configured_machine,
    )
    machine = configured_machine or resolve_execution_machine(
        context.hostname, guest=platform == "wsl", configured=False,
        load_entries=lambda: entries,
    )
    entry = entries.get(context.customization_key) if context.customization_key else None
    return machine, platform, entry
