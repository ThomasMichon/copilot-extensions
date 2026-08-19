#!/usr/bin/env python3
"""Profiles-matrix axes derived from ``machines.yaml`` (the canonical roster).

The Worktree Picker's Profiles view is a **host x target** matrix. Rather than
hardcode the axes (the prototype's ``engine.HOST_COLS`` / ``_TARGET_ENVS``),
both data sources derive them here so adding a machine to ``machines.yaml``
updates the matrix automatically (effort ``worktree-picker-tty-overhaul``).

- **HOST columns** = every ``copilot`` machine's *native-terminal* environment
  -- Windows or native Linux. A WSL environment is reached *through* a Windows
  host's terminal, so it is never its own host column.
- **TARGET rows** = every ``copilot`` machine x environment (WSL included);
  each becomes two rows (agent / shell) in the engine.

Both helpers degrade to an empty list when ``machines.yaml`` is unavailable;
the engine then falls back to its built-in default axes.
"""
from __future__ import annotations

from .. import config as cfg

# machines.yaml environment name -> the picker's short env label (and C_ENV key).
_ENV_LABEL = {"windows": "Win", "wsl": "WSL", "linux": "Linux"}
# Native-terminal envs that can be a HOST column (never a remote-only WSL).
_HOST_ENVS = {"windows", "linux"}
# Compact host-column env suffix.
_HOST_SHORT = {"Win": "Win", "WSL": "WSL", "Linux": "Lx"}


def _entries():
    """The ``machines.yaml`` roster for the active project, or ``{}``."""
    try:
        config = cfg.load_config()
        return cfg.load_machines_yaml(config.default_repo.anchor)
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


def host_cols():
    """Profiles HOST columns: ``[(label, machine_key, env_label), ...]``.

    One per copilot machine's native-terminal env (Windows / native Linux). The
    persisted/matched identity is the machine's **roster key** (its canonical
    full name); the visible header uses that same full name.
    """
    out = []
    for m, name, elabel in _copilot_envs():
        if name in _HOST_ENVS:
            short = _HOST_SHORT.get(elabel, elabel)
            out.append((f"{m.key}\u00b7{short}", m.key, elabel))
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
    plat = cfg.detect_platform()
    try:
        config = cfg.load_config()
        config_machine = (config.machine or "").lower()
    except Exception:
        config_machine = ""
    for m, name, elabel in _copilot_envs():
        is_local_machine = (
            m.key.lower() == host_key
            or (getattr(m, "hostname", "") or "").lower() == host_key
            or m.key.lower() == config_machine
            or (getattr(m, "alias", "") or "").lower() == config_machine
        )
        if is_local_machine and name == plat:
            return (m.key, elabel)
    return data_local.LOCAL

