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
import os
import signal
import socket
import subprocess
import threading

from .. import config as cfg
from . import data_local, derive, roster

# Shared display surface so the engine treats this exactly like ``data_local``.
# ``LOCAL`` is resolved from the actual local source below (so it carries the
# machine's ``machines.yaml`` display name, matching the tab descriptors) with
# ``data_local.LOCAL`` as the fallback when the registry is unavailable.
LOCAL_LABEL = data_local.LOCAL_LABEL
bucket = derive.bucket
for_machine = derive.for_machine
# Profiles-matrix axes are config-bound from machines.yaml (same roster).
host_cols = roster.host_cols
target_envs = roster.target_envs
# Repo name + default branch for the top bar -- project config, not hardcoded
# (shared with data_local; both resolve the same active-project config).
REPO = data_local.REPO
BRANCH = data_local.BRANCH

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
                 use_classify=True, timeout=20, alias="", shell="bash"):
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


def profiles_argv(machine, env, *, action, set_json=None, no_mirror=False):
    """SSH argv to run ``profiles get|apply`` on a remote host/env.

    Returns the ssh argv, or ``None`` for the local host or an unknown /
    not-ready target (the caller runs the local op in-process). ``set_json`` is
    the column payload for ``apply``.
    """
    project = _project()
    for s in _build_sources():
        if s.machine == machine and s.env == env:
            if s.local or not s.ready or not s.alias:
                return None
            if action == "get":
                inner = f"{project} profiles get --json"
            else:  # apply
                flags = " --no-mirror" if no_mirror else ""
                payload = (set_json or "[]").replace("'", "'\\''")
                inner = (f"{project} profiles apply --json{flags} "
                         f"--set '{payload}'")
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


def load_profile_column(machine, env):
    """Read a host's terminal-profile column (local in-process / remote SSH)."""
    from . import profiles_io
    return profiles_io.load_column(machine, env)


def apply_profile_column(machine, env, sels, *, mirror=True):
    """Persist a host's terminal-profile column. Returns ``(ok, detail)``."""
    from . import profiles_io
    return profiles_io.apply_column(machine, env, sels, mirror=mirror)


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
        # Detach the child from the console's stdin. Without this, an ``ssh``
        # child inherits the terminal's keyboard input and *reads* it (ssh
        # forwards stdin to the remote) -- so while the picker's background
        # load fan-out is alive, the operator's keystrokes are swallowed by
        # the ssh processes instead of reaching Textual's input reader
        # (which reads the same console handle). Input only "unblocks" once
        # the SSH calls finish. DEVNULL gives ssh an empty stdin (instant
        # EOF) and leaves the console input with the TUI.
        stdin=subprocess.DEVNULL,
    )


def _kill_proc_tree(proc):
    """Best-effort terminate a prefetch child *and* its process group.

    Killing the local ``ssh`` also drops the channel, so the remote
    ``agent-worktrees list`` it was driving dies with it -- which is the whole
    point: don't leave a heavy git-classification churning on the machine the
    operator is about to hand off into.
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.terminate()
        else:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass


def _fetch(source: Source, runner=None):
    """Run one source's list command and return normalized worktree records.

    Local sources load in-process. Remotes run over SSH, retrying without
    ``--classify`` when the remote agent-worktrees is too old to recognize it.

    ``runner`` runs the subprocess (default :func:`_run`); :class:`LiveLoader`
    passes its own tracked-and-killable runner so a picker exit can cancel any
    in-flight prefetch.
    """
    runner = runner or _run
    if source.local:
        return data_local.load(source.machine, source.env)

    proc = runner(source.argv, source.timeout)
    if proc.returncode != 0 and _is_classify_unsupported(proc.stderr):
        # Older remote: drop --classify and retry (rows will lack canonical
        # state but still load).
        retry = [a.replace(" --classify", "") for a in source.argv]
        source.argv = retry
        source.use_classify = False
        proc = runner(retry, source.timeout)
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
        # In-flight prefetch ssh children, tracked so a picker exit can kill
        # them (otherwise a quick selection orphans them -- they reparent to
        # init and keep churning git-classification on the target machine,
        # starving the Copilot session we just launched there).
        self._procs = []
        self._procs_lock = threading.Lock()
        self._cancelled = threading.Event()
        for s in all_sources:
            self._state[s.key] = "loading" if s.ready else "failed"
            self._records[s.key] = []

    def start(self):
        derive.NOW = _dt.datetime.now()
        # Every source -- local included -- loads on its own daemon thread so
        # the picker paints and accepts keys the instant it mounts; rows stream
        # in (local + remote alike) via the engine's render tick as each source
        # resolves. Local was once run synchronously here on the assumption it's
        # "fast" (#1432), but a real machine's git-classification of many
        # worktrees can take multiple seconds -- long enough to freeze the whole
        # TUI (no paint, no arrow keys) until it finished. Threading it keeps
        # interaction immediate and the local tab simply shows the connect
        # spinner until its records arrive, exactly like the remotes.
        for s in self._sources:
            threading.Thread(
                target=self._load_one, args=(s,),
                name=f"load-{s.machine}-{s.env}", daemon=True,
            ).start()

    def reload(self, machine, env):
        """Re-fetch one source now (e.g. after a Maintenance op changed it).

        Every source -- local included -- re-threads so a post-maintenance
        refresh never blocks the UI (the local git-classification can take
        seconds); the tab shows the connect spinner until its fresh records
        arrive. Unknown / not-ready sources are a no-op. Returns True when a
        matching source was found (#1421, live re-render).
        """
        for s in self._sources:
            if s.key == (machine, env):
                with self._lock:
                    self._state[s.key] = "loading"
                threading.Thread(
                    target=self._load_one, args=(s,),
                    name=f"reload-{s.machine}-{s.env}", daemon=True,
                ).start()
                return True
        return False

    def cancel(self):
        """Stop loading and kill any in-flight prefetch ssh children.

        Idempotent; called from the picker's teardown (Textual ``on_unmount``)
        so a launch decision never leaves orphaned ``ssh ... list`` processes
        behind. Safe to call when nothing is in flight.
        """
        self._cancelled.set()
        with self._procs_lock:
            procs = list(self._procs)
        for p in procs:
            _kill_proc_tree(p)

    def _spawn(self, argv, timeout):
        """Tracked, killable runner for prefetch subprocesses.

        Mirrors :func:`_run` but registers the live :class:`subprocess.Popen`
        so :meth:`cancel` can terminate it (and, on POSIX, its whole process
        group). Returns a :class:`subprocess.CompletedProcess` so ``_fetch`` is
        agnostic to which runner produced it.
        """
        if self._cancelled.is_set():
            raise RuntimeError("cancelled")
        kwargs = dict(
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            # Never inherit the console stdin: an ``ssh`` child would otherwise
            # read the terminal's keyboard input out from under Textual's input
            # reader, freezing the picker's keys until the load fan-out exits.
            stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
        )
        if os.name == "posix":
            kwargs["start_new_session"] = True   # own group -> killpg on cancel
        else:
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen(argv, **kwargs)
        with self._procs_lock:
            self._procs.append(proc)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_proc_tree(proc)
            out, err = proc.communicate()
        finally:
            with self._procs_lock:
                if proc in self._procs:
                    self._procs.remove(proc)
        return subprocess.CompletedProcess(argv, proc.returncode, out, err)

    def _load_one(self, source: Source):
        try:
            recs = _fetch(source, runner=self._spawn)
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
