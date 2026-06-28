#!/usr/bin/env python3
"""Multi-machine SSH data source for the Worktree Picker TUI.

Drop-in replacement for ``data_local`` exposing the same surface the engine
needs (``LOCAL`` / ``LOCAL_LABEL`` / ``machines()`` / ``bucket`` /
``for_machine`` / ``load()``) plus ``make_loader()`` for the engine's live
mode. The roster comes from the canonical ``machines.yaml`` registry (via
``config.load_machines_yaml``), so display names, env labels, SSH aliases and
shells never drift from config.

A :class:`LiveLoader` runs ``<project> list --json --classify --mux-details``
on a background daemon thread per machine: the local machine in-process (reusing
``data_local.load``, no subprocess), every reachable remote over its facility
SSH alias. The picker shows the connect spinner while a machine loads and
resolves it to ``ready`` (data) or ``failed`` (unreachable / errored).

This module only *reads* worktree listings -- it never creates, opens, cleans,
or syncs anything.

Graceful degradation: a remote running an agent-worktrees older than dev59 does
not recognize ``--classify``. When the list command fails with an
"unrecognized arguments" error mentioning ``--classify``, the loader retries
without it, so older remotes still load (their rows just lack canonical state).
"""
from __future__ import annotations

import datetime as _dt
import json
import socket
import subprocess
import threading

from .. import config as cfg
from . import data_local, derive

# Shared display surface so the engine treats this exactly like ``data_local``.
# ``LOCAL`` is resolved from the actual local source below (so it carries the
# machine's ``machines.yaml`` display name, matching the tab descriptors) with
# ``data_local.LOCAL`` as the fallback when the registry is unavailable.
LOCAL_LABEL = data_local.LOCAL_LABEL
bucket = derive.bucket
for_machine = derive.for_machine

# machines.yaml environment name -> the picker's short env label (and C_ENV key).
_ENV_LABEL = {"windows": "Win", "wsl": "WSL", "linux": "Linux"}

# Base list args shared by every source. ``--include-other-platforms`` is added
# for Windows targets so a Windows machine's WSL worktrees come back too.
_LIST_ARGS = "list --json --classify --mux-details"
_LIST_ARGS_WIN = _LIST_ARGS + " --include-other-platforms"


class Source:
    """One machine/environment the picker loads worktrees from.

    ``machine``/``env`` are the display labels (``machines.yaml`` display name +
    short env label) and must match this module's ``machines()`` descriptors so
    the engine's per-tab filtering and "this host" detection line up.
    """

    def __init__(self, machine, env, argv, *, local=False, ready=True,
                 use_classify=True, timeout=45, alias="", shell="bash"):
        self.machine = machine        # display_name from machines.yaml
        self.env = env                # Win | WSL | Linux
        self.argv = argv              # subprocess argv (None for the local src)
        self.local = local
        self.ready = ready
        self.use_classify = use_classify
        self.timeout = timeout
        self.alias = alias            # SSH alias (remote sources only)
        self.shell = shell            # pwsh | bash (for remote command wrapping)

    @property
    def key(self):
        return (self.machine, self.env)


def _local_identity() -> tuple[str, str]:
    """(hostname-key, platform-name) for the machine this picker runs on."""
    return socket.gethostname().split(".")[0].lower(), cfg.detect_platform()


def _project() -> str:
    try:
        return cfg.project_name()
    except (RuntimeError, ValueError):
        return "agent-worktrees"


def _list_args(shell: str, *, classify: bool) -> str:
    win = shell == "pwsh"
    args = _LIST_ARGS_WIN if win else _LIST_ARGS
    if not classify:
        args = args.replace(" --classify", "")
    return args


def _argv_for(shell: str, alias: str, project: str, *, classify: bool):
    """Remote list argv for a machine/env: pwsh on Windows, bash elsewhere."""
    cmd = f"{project} {_list_args(shell, classify=classify)}"
    if shell == "pwsh":
        return ["ssh", alias, f"pwsh -NoProfile -Command '{cmd}'"]
    return ["ssh", alias, f"bash -lc '{cmd}'"]


def _build_sources():
    """Derive machine/env sources from ``machines.yaml`` (the canonical roster).

    Skips ``copilot: false`` machines entirely. A machine with
    ``ssh.ready: false`` is kept as a disabled tab (never contacted). The local
    machine's matching env becomes the in-process local source; its other
    environments and every remote env go over SSH.
    """
    config = cfg.load_config()
    repo = config.default_repo
    try:
        entries = cfg.load_machines_yaml(repo.anchor)
    except (FileNotFoundError, ValueError):
        entries = {}

    project = _project()
    local_key, local_plat = _local_identity()
    config_machine = (config.machine or "").lower()

    out: list[Source] = []
    for key, m in entries.items():
        if not m.copilot:
            continue
        is_local_machine = (
            key.lower() == local_key
            or key.lower() == config_machine
            or (m.alias and m.alias.lower() == config_machine)
        )
        for ssh_env in m.ssh_environments:
            ename = (ssh_env.name or "").lower()
            elabel = _ENV_LABEL.get(ename, ename.title() or "?")
            shell = ssh_env.shell or ("pwsh" if ename == "windows" else "bash")
            is_local = is_local_machine and ename == local_plat
            if is_local:
                out.append(Source(m.display_name, elabel, None, local=True,
                                  ready=True))
            elif m.ssh_ready:
                argv = _argv_for(shell, ssh_env.alias, project, classify=True)
                out.append(Source(m.display_name, elabel, argv, ready=True,
                                  alias=ssh_env.alias, shell=shell))
            else:
                out.append(Source(m.display_name, elabel, None, ready=False,
                                  alias=ssh_env.alias, shell=shell))
    return out


def _wrap_remote(shell: str, alias: str, inner: str):
    """SSH argv that runs *inner* under the right login shell on *alias*."""
    if shell == "pwsh":
        return ["ssh", alias, f"pwsh -NoProfile -Command '{inner}'"]
    return ["ssh", alias, f"bash -lc '{inner}'"]


def remote_op_argv(machine, env, op, worktree_id, *, include_unused=False,
                   include_conversations=False, force=False):
    """Build the SSH argv to run one maintenance op on a remote machine/env.

    ``op`` is ``"cleanup"`` or ``"sync"``. Returns the ssh argv, or ``None`` for
    the local host or an unknown / not-ready target (the caller runs local ops
    in-process). The remote runs the project binstub's JSON per-worktree CLI.
    """
    project = _project()
    for s in _build_sources():
        if s.machine == machine and s.env == env:
            if s.local or not s.ready or not s.alias:
                return None
            if op == "cleanup":
                flags = " --clean --json"
                if force:
                    flags += " --force"
                if include_unused:
                    flags += " --include-unused"
                if include_conversations:
                    flags += " --include-conversations"
                inner = f"{project} cleanup --worktree-id {worktree_id}{flags}"
            else:  # sync
                inner = f"{project} sync --worktree-id {worktree_id} --json"
            return _wrap_remote(s.shell, s.alias, inner)
    return None


def machines():
    """Ordered machine-tab descriptors: (label, machine, env, reachable).

    ``reachable`` is the ``machines.yaml`` ``ssh.ready`` flag (the local source
    is always reachable): ready machines are attempted (spinner -> ✓/✗);
    not-ready machines render as a disabled tab and are never contacted.
    """
    return [
        (f"{s.machine} {s.env}", s.machine, s.env, s.ready)
        for s in _build_sources()
    ]


def _resolve_local() -> tuple[str, str]:
    """(machine, env) of this host, using the registry display name when known.

    Falls back to ``data_local.LOCAL`` (hostname-based) if the local machine is
    not represented in ``machines.yaml``.
    """
    for s in _build_sources():
        if s.local:
            return s.key
    return data_local.LOCAL


LOCAL = _resolve_local()


def load(machine: str | None = None, env: str | None = None):
    """Synchronous local-only load (live mode streams via :class:`LiveLoader`).

    Provided so this source stays swap-compatible with ``data_local`` for the
    non-live code path; returns just this host's worktrees.
    """
    return data_local.load(LOCAL[0], LOCAL[1])


def make_loader():
    """Build the background per-machine loader the engine drives in live mode."""
    return LiveLoader(_build_sources())


def _extract_json(text: str):
    """Parse the first JSON object out of command output.

    Login shells / pwsh can emit banner noise before the JSON, so locate the
    first ``{`` and let the decoder consume just that object.
    """
    i = text.find("{")
    if i < 0:
        raise RuntimeError("no JSON in output")
    obj, _end = json.JSONDecoder().raw_decode(text[i:])
    return obj


def _is_classify_unsupported(stderr: str) -> bool:
    s = (stderr or "").lower()
    return "unrecognized arguments" in s and "--classify" in s


def _run(argv, timeout):
    return subprocess.run(
        argv, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def _fetch(source: Source):
    """Run one source's list command and return normalized worktree records.

    Local sources load in-process. Remotes run over SSH, retrying without
    ``--classify`` when the remote agent-worktrees is too old to recognize it.
    """
    if source.local:
        return data_local.load(source.machine, source.env)

    proc = _run(source.argv, source.timeout)
    if proc.returncode != 0 and _is_classify_unsupported(proc.stderr):
        # Older remote: drop --classify and retry (rows will lack canonical
        # state but still load).
        retry = [a.replace(" --classify", "") for a in source.argv]
        source.argv = retry
        source.use_classify = False
        proc = _run(retry, source.timeout)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError(err[-1] if err else f"exit {proc.returncode}")
    data = _extract_json(proc.stdout)
    return [derive.norm(w, source.machine, source.env)
            for w in data.get("worktrees", [])]


class LiveLoader:
    """Background, per-machine loader feeding the picker's spinner -> resolve.

    On :meth:`start`, spawns one daemon thread per *ready* source. Each thread
    runs its list command and records either ``ready`` (with normalized
    worktrees) or ``failed`` (on any error/timeout). The UI polls :meth:`state`
    and :meth:`records` from its render tick -- a failed remote never crashes or
    hangs the UI. Not-ready sources are seeded ``failed`` and never contacted.
    """

    def __init__(self, sources=None):
        all_sources = list(sources if sources is not None else _build_sources())
        self._sources = [s for s in all_sources if s.ready]
        self._lock = threading.Lock()
        self._state = {}     # (machine, env) -> loading|ready|failed
        self._records = {}   # (machine, env) -> [normalized record, ...]
        self._error = {}     # (machine, env) -> str (last error)
        for s in all_sources:
            self._state[s.key] = "loading" if s.ready else "failed"
            self._records[s.key] = []

    def start(self):
        derive.NOW = _dt.datetime.now()
        for s in self._sources:
            threading.Thread(
                target=self._load_one, args=(s,),
                name=f"load-{s.machine}-{s.env}", daemon=True,
            ).start()

    def _load_one(self, source: Source):
        try:
            recs = _fetch(source)
        except Exception as exc:  # any failure -> failed state
            with self._lock:
                self._state[source.key] = "failed"
                self._error[source.key] = str(exc).strip() or type(exc).__name__
            return
        with self._lock:
            self._records[source.key] = recs
            self._state[source.key] = "ready"

    def state(self, machine, env):
        with self._lock:
            return self._state.get((machine, env), "loading")

    def records(self):
        """Flat list of every normalized worktree from machines that are ready."""
        with self._lock:
            out = []
            for key, recs in self._records.items():
                if self._state.get(key) == "ready":
                    out.extend(recs)
            return out

    def counts(self):
        """(ready, loading, failed) machine counts for the status note."""
        with self._lock:
            vals = list(self._state.values())
        return (
            sum(1 for v in vals if v == "ready"),
            sum(1 for v in vals if v == "loading"),
            sum(1 for v in vals if v == "failed"),
        )

    def error(self, machine, env):
        with self._lock:
            return self._error.get((machine, env))
