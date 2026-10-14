#!/usr/bin/env python3
"""Profiles-matrix axes derived from ``machines.yaml`` (the canonical roster).

The Worktree Picker's Profiles view is a **host x target** matrix. Rather than
hardcode the axes (the prototype's ``engine.HOST_COLS`` / ``_TARGET_ENVS``),
both data sources derive them here so adding a machine to ``machines.yaml``
updates the matrix automatically (effort ``worktree-picker-tty-overhaul``).

- **HOST columns** = every ``copilot`` logical machine's execution environment,
  including independently addressable WSL machines.
- **TARGET rows** = every ``copilot`` machine x environment (WSL included);
  each becomes two rows (agent / shell) in the engine.

Both helpers degrade to an empty list when ``machines.yaml`` is unavailable;
the engine then falls back to its built-in default axes.
"""
from __future__ import annotations

from machine_transport import (
    find_machine_entry,
    local_execution_platform,
)

from .. import project_config as cfg

# machines.yaml environment name -> the picker's short env label (and C_ENV key).
_ENV_LABEL = {"windows": "Win", "wsl": "WSL", "linux": "Linux"}
# Execution environments that can be a HOST column.
_HOST_ENVS = {"windows", "wsl", "linux"}
# Compact host-column env suffix.
_HOST_SHORT = {"Win": "Win", "WSL": "WSL", "Linux": "Lx"}


def _entries():
    """The ``machines.yaml`` roster for the active project, or ``{}``."""
    try:
        config = cfg.load_config()
        return cfg.load_machines_yaml(config.default_repo.anchor)
    except ValueError:
        raise
    except Exception:
        return {}


def _copilot_envs():
    """Yield ``(machine_obj, env_name, env_label)`` for every copilot machine x
    environment in roster order."""
    for _key, m in _entries().items():
        if not getattr(m, "copilot", True):
            continue
        for env in getattr(m, "ssh_environments", []) or []:
            name = (env.name or "").lower()
            yield m, name, _ENV_LABEL.get(name, name.title() or "?")
        if m.execution_platform and not m.ssh_environments:
            name = m.execution_platform
            yield m, name, _ENV_LABEL.get(name, name.title())


def display_machine(entry):
    """Physical association is cosmetic; all matrix keys stay logical."""
    return entry.physical_host or entry.key


def apply_display_machine(sources, entry):
    for source in sources:
        source.display_machine = display_machine(entry)
        source.source_label = f"{source.display_machine} / {source.env}"


def local_entry(entries, config_machine, hostname, execution_platform):
    """Resolve the running execution space, not every sibling on its host."""
    selected = find_machine_entry(entries, config_machine, reject_ambiguous=True)
    if (
        selected is not None and selected.execution_platform
        and selected.execution_platform != execution_platform
        and not selected.legacy_key
    ):
        raise ValueError(
            f"configured machine {config_machine!r} is not the local {execution_platform} space"
        )
    for name in (config_machine, hostname):
        if not name:
            continue
        entry = find_machine_entry(
            entries, name, reject_ambiguous=True,
            execution_platform=execution_platform,
        )
        if entry is not None:
            return entry
    return None


def host_cols():
    """Profiles HOST columns: ``[(label, machine_key, env_label), ...]``.

    One per copilot machine's execution env (Windows / WSL / native Linux). The
    persisted/matched identity is the machine's **roster key** (its canonical
    full name); the visible header uses its physical association when declared.
    """
    out = []
    for m, name, elabel in _copilot_envs():
        if name in _HOST_ENVS:
            short = _HOST_SHORT.get(elabel, elabel)
            out.append((f"{display_machine(m)}\u00b7{short}", m.key, elabel))
    return out


def target_envs():
    """Profiles TARGET ``(machine_key, env_label)`` pairs (WSL included).

    The machine axis is the roster **key** (canonical full name), not the
    cosmetic ``display_name`` -- so a persisted column keys on stable identity.
    """
    return [(m.key, elabel) for m, _name, elabel in _copilot_envs()]


def local_host():
    """This machine's ``(machine_key, env_label)`` in roster vocabulary.

    Uses the ``machines.yaml`` **key** (canonical full name) + short env label,
    matching ``host_cols`` / ``target_envs`` so the self.agent diagonal lines up
    with the grid. Falls back to ``data_local.LOCAL`` (hostname-based) when the
    local machine is not represented in the roster.
    """
    import socket

    from . import data_local

    host_key = socket.gethostname().split(".")[0].lower()
    plat = local_execution_platform()
    try:
        config = cfg.load_config()
        config_machine = (config.machine or "").lower()
    except Exception:
        config_machine = ""
    entries = _entries()
    m = local_entry(entries, config_machine, host_key, plat)
    if m is not None and m.copilot:
        for env in m.ssh_environments:
            name = (env.name or "").lower()
            if name == plat:
                return (m.key, _ENV_LABEL.get(name, name.title()))
        return (m.key, _ENV_LABEL.get(plat, plat.title()))
    return data_local.LOCAL
