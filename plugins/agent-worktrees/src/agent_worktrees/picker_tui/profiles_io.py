#!/usr/bin/env python3
"""Load / apply terminal-profile columns for the Picker's Profiles grid.

Bridges the engine's host x target grid to the persisted **own-column** model
(``agent_worktrees.profiles``): each host machine owns one column. Reading the
whole grid means reading every reachable host's column; applying a column writes
*that host's* config (and mirrors it to its terminal profiles).

- **Local host** -- read/write in-process via ``agent_worktrees.profiles`` and
  mirror via ``__main__._mirror_terminal_profiles`` (lazy-imported to keep the
  picker_tui <-> __main__ cycle broken).
- **Remote host** -- shell ``<project> profiles get|apply`` over the machine's
  facility SSH alias (argv from ``data_ssh.profiles_argv``).

The SSH runner is injected (default: real subprocess) so tests drive this with
no network.
"""
from __future__ import annotations

import json

from .. import profiles as profiles_mod
from . import data_ssh, roster

TargetSel = profiles_mod.TargetSel

# Sentinel for a host column that could NOT be loaded -- an unreachable remote,
# or a remote on an agent-worktrees too old to have the ``profiles`` subcommand.
# This is distinct from ``None`` (a reachable, compatible host that simply has
# no explicit selection yet -> legacy "all-on"): UNAVAILABLE means "we cannot
# know this host's real selection", so the Picker must render the column
# read-only and never write a fabricated selection back over SSH (#1370).
UNAVAILABLE = object()


def _default_runner(argv, timeout=20):
    return data_ssh._run(argv, timeout)


def _local_key():
    return roster.local_host()


def load_column(machine, env, *, runner=_default_runner):
    """Return (machine, env)'s terminal column, or a load-status sentinel.

    Three outcomes:

    - a set of :class:`TargetSel` -- a **managed** host's real selection.
    - ``None`` -- a reachable, compatible host that carries **no explicit
      selection yet** (legacy/unmanaged). The caller treats every target as
      selected, matching the installer mirror's historical emit-everything
      behavior, and the column stays editable (a modern remote can accept an
      Apply).
    - ``UNAVAILABLE`` -- the host's column could **not be loaded**: an
      unreachable/not-ready remote, an SSH error/timeout, or a remote running an
      agent-worktrees too old to have the ``profiles`` subcommand. The caller
      renders the column read-only ("upgrade / unavailable") and never writes it
      back, because a remote Apply there would fail and any displayed selection
      would be fabricated (#1370).

    Local host reads its own config in-process (always reachable -> set/``None``,
    never ``UNAVAILABLE``); a remote host is queried over SSH.
    """
    from .. import config as cfg

    if (machine, env) == _local_key():
        cfg_path = cfg.default_config_path()
        if not profiles_mod.has_selection(cfg_path):
            return None
        sels = profiles_mod.load_selection(cfg_path)
        return set(profiles_mod.normalize_selection(sels, machine, env))

    argv = data_ssh.profiles_argv(machine, env, action="get")
    if not argv:
        # No SSH argv -> the host is not reachable/ready; we cannot read or write
        # its column, so it is unavailable (not legacy all-on).
        return UNAVAILABLE
    try:
        proc = runner(argv, 20)
    except Exception:
        # A transient/hard SSH failure: we can't know the remote's selection, so
        # mark the column unavailable rather than fabricate an all-on selection
        # we'd then try (and fail) to write back.
        return UNAVAILABLE
    if proc.returncode != 0:
        # Nonzero commonly means an older remote without the ``profiles``
        # subcommand (or a genuine error) -> unavailable, read-only.
        return UNAVAILABLE
    try:
        data = data_ssh._extract_json(proc.stdout)
    except Exception:
        return UNAVAILABLE
    if not data.get("managed", False):
        # Reachable + compatible, but no selection yet -> legacy all-on.
        return None
    out = {profiles_mod.self_diagonal(machine, env)}
    for t in data.get("targets", []):
        if isinstance(t, dict) and t.get("machine") and t.get("env"):
            out.add(TargetSel(t["machine"], t["env"],
                              (t.get("kind") or "agent")))
    return out


def apply_column(machine, env, sels, *, mirror=True, runner=_default_runner):
    """Persist (machine, env)'s column. Returns ``(ok, detail)``.

    Local host writes its config (and mirrors when ``mirror``); a remote host is
    written over SSH via ``profiles apply``. ``sels`` is an iterable of
    ``TargetSel``; the locked self.agent target is always included by the
    persistence layer.
    """
    from .. import config as cfg

    sels = list(sels)
    if (machine, env) == _local_key():
        profiles_mod.save_selection(
            cfg.default_config_path(), sels,
            self_machine=machine, self_env=env)
        mirrored = False
        if mirror:
            from .. import __main__ as _m
            mirrored = _m._mirror_terminal_profiles()
        return True, ("mirrored" if mirrored else "saved")

    payload = json.dumps([s.as_dict() for s in sels])
    argv = data_ssh.profiles_argv(
        machine, env, action="apply", set_json=payload,
        no_mirror=not mirror)
    if not argv:
        return False, "unreachable"
    try:
        proc = runner(argv, 30)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip().splitlines()
            return False, (err[-1] if err else f"exit {proc.returncode}")
    except Exception as exc:
        return False, str(exc) or type(exc).__name__
    return True, "pushed"
