#!/usr/bin/env python3
"""Real local data source for the Worktree Picker TUI.

Exposes the same surface the engine's prototype sources did
(``LOCAL`` / ``LOCAL_LABEL`` / ``machines()`` / ``load()`` / ``bucket`` /
``for_machine``), but backed by the real tracking store + git classification
on *this* machine. Slice 1 of the port covers the local machine only; remote
machines arrive via an SSH source + async loader in a later slice.
"""
from __future__ import annotations

import datetime as _dt
import socket
from pathlib import Path

from .. import config as cfg
from .. import sessions, tracking
from . import derive, roster

bucket = derive.bucket
for_machine = derive.for_machine
host_cols = roster.host_cols
target_envs = roster.target_envs

_ENV_LABEL = {"windows": "Win", "wsl": "WSL", "linux": "Linux"}


def _local_identity() -> tuple[str, str]:
    host = socket.gethostname().split(".")[0]
    plat = cfg.detect_platform()
    return host, _ENV_LABEL.get(plat, plat.title())


LOCAL = _local_identity()
LOCAL_LABEL = f"{LOCAL[0]} · {LOCAL[1].lower()}"


def machines():
    """Machine-tab descriptors. Slice 1: the local machine only."""
    m, e = LOCAL
    return [(f"{m} {e}", m, e, True)]


def load_profile_column(machine, env):
    """Read a host's terminal-profile column (local in-process / remote SSH)."""
    from . import profiles_io
    return profiles_io.load_column(machine, env)


def apply_profile_column(machine, env, sels, *, mirror=True):
    """Persist a host's terminal-profile column. Returns ``(ok, detail)``."""
    from . import profiles_io
    return profiles_io.apply_column(machine, env, sels, mirror=mirror)


def load(machine: str | None = None, env: str | None = None):
    """Normalized records for this machine's worktrees (tracking + classify).

    *machine*/*env* default to this host's identity (``LOCAL``). The SSH source
    overrides them so the local machine's rows carry its ``machines.yaml``
    display name and env label, matching the multi-machine tab descriptors.
    """
    # Lazy import to avoid a picker_tui <-> __main__ import cycle.
    from ..__main__ import _classify_records, _worktree_to_dict

    derive.NOW = _dt.datetime.now()
    tracking_path = cfg.tracking_dir()
    plat = cfg.detect_platform()
    records = tracking.list_records(tracking_path, platform_filter=plat)
    records = [
        r for r in records
        if r.worktree_path
        and Path(r.worktree_path).exists()
        and (Path(r.worktree_path) / ".git").exists()
    ]
    if not records:
        return []
    session_ctx = sessions.scan_sessions_fast(records)
    mux_map = sessions.mux_status_many([r.worktree_id for r in records])
    state_map = _classify_records(records, session_ctx)
    machine = machine if machine is not None else LOCAL[0]
    env = env if env is not None else LOCAL[1]
    out = []
    for rec in records:
        raw = _worktree_to_dict(
            rec, mux_info=mux_map.get(rec.worktree_id),
            session_ctx=session_ctx, state_info=state_map.get(rec.worktree_id),
        )
        out.append(derive.norm(raw, machine, env))
    return out
