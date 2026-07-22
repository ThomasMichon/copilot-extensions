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

import base64
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


def _list_args(shell: str, *, classify: bool, reconcile: bool = False) -> str:
    win = shell == "pwsh"
    args = _LIST_ARGS_WIN if win else _LIST_ARGS
    if not classify:
        args = args.replace(" --classify", "")
    if reconcile:
        # Reconcile this machine's own PR states against the provider during the
        # list (and persist the correction), so a remote tab stops showing a
        # merged-elsewhere worktree as having an open PR (#2102).
        args = args + " --reconcile-prs"
    return args


def _pwsh_remote(cmd: str) -> str:
    """Remote pwsh invocation that survives the remote sshd's default shell.

    Uses ``-EncodedCommand`` (base64 of UTF-16LE) instead of ``-Command '<cmd>'``.
    A plain single-quoted ``-Command`` is mangled when the remote sshd's default
    shell is **cmd.exe** (e.g. a dtssh Windows host, where DefaultShell is unset):
    cmd.exe passes the quotes through and pwsh evaluates the single-quoted text as
    a *string literal* -- echoing it instead of running it, so the picker gets a
    non-JSON line back and the machine shows as failed. EncodedCommand carries no
    shell-special characters, so it executes correctly regardless of the remote
    default shell (cmd.exe or pwsh)."""
    enc = base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")
    return f"pwsh -NoProfile -EncodedCommand {enc}"


def _argv_for(shell: str, alias: str, project: str, *, classify: bool,
              reconcile: bool = False):
    """Remote list argv for a machine/env: pwsh on Windows, bash elsewhere."""
    cmd = f"{project} {_list_args(shell, classify=classify, reconcile=reconcile)}"
    if shell == "pwsh":
        return ["ssh", alias, _pwsh_remote(cmd)]
    return ["ssh", alias, f"bash -lc '{cmd}'"]


def _build_sources():
    """Derive machine/env sources from ``machines.yaml`` (the canonical roster).

    Skips ``copilot: false`` machines entirely. The local machine's matching env
    always becomes the in-process local source -- **it never needs an SSH
    profile of its own** (the picker runs there): even a machine with no SSH
    environment, or one whose ``ssh.ready`` is false, still gets a working local
    tab. Every *other* env is contacted over SSH only when it actually has an
    SSH profile (a non-empty alias) and the machine is ``ssh.ready``; an env
    with no alias is rendered as a disabled tab and never connected to, and a
    ``ssh.ready: false`` machine's remote envs stay disabled tabs.
    """
    config = cfg.load_config()
    repo = config.default_repo
    try:
        entries = cfg.load_machines_yaml(repo.anchor)
    except (FileNotFoundError, ValueError):
        entries = {}

    project = _project()
    local_key, local_plat = _local_identity()
    local_elabel = _ENV_LABEL.get(local_plat, local_plat.title() or "?")
    config_machine = (config.machine or "").lower()

    out: list[Source] = []
    for key, m in entries.items():
        if not m.copilot:
            continue
        is_local_machine = (
            key.lower() == local_key
            or (getattr(m, "hostname", "") or "").lower() == local_key
            or key.lower() == config_machine
            or (m.alias and m.alias.lower() == config_machine)
        )
        local_env_added = False
        for ssh_env in m.ssh_environments:
            ename = (ssh_env.name or "").lower()
            elabel = _ENV_LABEL.get(ename, ename.title() or "?")
            shell = ssh_env.shell or ("pwsh" if ename == "windows" else "bash")
            alias = ssh_env.alias or ""
            is_local = is_local_machine and ename == local_plat
            if is_local:
                # Local env: in-process, no SSH profile required.
                out.append(Source(m.display_name, elabel, None, local=True,
                                  ready=True))
                local_env_added = True
            elif not alias:
                # No SSH profile for this env -- never try to connect to it;
                # surface it as a disabled tab.
                out.append(Source(m.display_name, elabel, None, ready=False,
                                  alias="", shell=shell))
            elif m.ssh_ready:
                argv = _argv_for(shell, alias, project, classify=True)
                out.append(Source(m.display_name, elabel, argv, ready=True,
                                  alias=alias, shell=shell))
            else:
                out.append(Source(m.display_name, elabel, None, ready=False,
                                  alias=alias, shell=shell))
        # Bypass: the current machine always gets a local source, even when it
        # has no SSH environment of its own in machines.yaml (or none matched
        # the running platform). The picker runs *here*, so it never needs to
        # SSH to itself.
        if is_local_machine and not local_env_added:
            out.append(Source(m.display_name, local_elabel, None, local=True,
                              ready=True))

    # Defensive fail-safe: guarantee a local source even when this machine is
    # entirely absent from machines.yaml (a freshly-provisioned box whose
    # self-entry hasn't reached the anchor yet, or a stale anchor checkout).
    # Without a local source the engine has no "this host" tab and the picker
    # crashes. The picker runs *here*, so a hostname-based local tab (from
    # ``data_local.LOCAL``) is always the correct, safe fallback.
    if not any(s.local for s in out):
        out.append(Source(data_local.LOCAL[0], data_local.LOCAL[1], None,
                          local=True, ready=True))
    return out


def _wrap_remote(shell: str, alias: str, inner: str):
    """SSH argv that runs *inner* under the right login shell on *alias*."""
    if shell == "pwsh":
        return ["ssh", alias, _pwsh_remote(inner)]
    return ["ssh", alias, f"bash -lc '{inner}'"]


def remote_op_argv(machine, env, op, worktree_id, *, include_unused=False,
                   include_conversations=False, force=False):
    """Build the SSH argv to run one maintenance op on a remote machine/env.

    ``op`` is ``"cleanup"``, ``"sync"``, ``"restart"``, or ``"finalize"``.
    Returns the ssh argv, or ``None`` for the local host or an unknown /
    not-ready target (the caller runs local ops in-process). The remote runs
    the project binstub's JSON per-worktree CLI.
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
            elif op == "restart":
                # ``restart`` takes the worktree id positionally (not
                # --worktree-id); the remote graceful double-Ctrl-C / mux
                # kill-session runs there and reports a single JSON object.
                inner = f"{project} restart {worktree_id} --json"
            elif op == "finalize":
                # ``finalize`` takes the worktree id positionally (like
                # ``restart``), not --worktree-id.
                inner = f"{project} finalize {worktree_id} --json"
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

    ``reachable`` is true only for the local source (always) and for a remote
    env that both has an SSH profile (a non-empty alias) and belongs to an
    ``ssh.ready`` machine: those are attempted (spinner -> ✓/✗). An env with no
    SSH profile, or one on a ``ssh.ready: false`` machine, renders as a disabled
    tab and is never contacted.
    """
    return [
        (f"{s.machine} {s.env}", s.machine, s.env, s.ready)
        for s in _build_sources()
    ]


def machine_key_map() -> dict[str, str]:
    """``display_name -> registry key`` from ``machines.yaml``.

    A machine's registry key is its canonical identity (lowercase; it doubles as
    the SSH-alias base) -- the value ``agent-worktrees get machine`` returns and
    that other facility tools (agent-dispatch, agent-bridge) match against. The
    picker's tab labels carry the *display* name, so a registered pivot that
    scopes its CLI ``{machine}`` needs this translation to hand over the identity,
    not the label. Best-effort: an unreadable roster yields ``{}`` (the caller
    then falls back to the display name). Uncached -- the engine caches the
    result for a session; keeping this pure keeps it trivially testable.
    """
    mapping: dict[str, str] = {}
    try:
        config = cfg.load_config()
        entries = cfg.load_machines_yaml(config.default_repo.anchor)
    except (FileNotFoundError, ValueError, AttributeError):
        return mapping
    for key, m in entries.items():
        display = getattr(m, "display_name", None)
        if display:
            mapping[display] = key
    return mapping


def machine_key(display_name: str | None) -> str | None:
    """The registry key (canonical identity) for a machine's ``display_name``,
    or the display name itself when the roster can't resolve it."""
    if not display_name:
        return display_name
    return machine_key_map().get(display_name, display_name)


def load_profile_column(machine, env):
    """Read a host's terminal-profile column (local in-process / remote SSH)."""
    from . import profiles_io
    return profiles_io.load_column(machine, env)


def apply_profile_column(machine, env, sels, *, mirror=True):
    """Persist a host's terminal-profile column. Returns ``(ok, detail)``."""
    from . import profiles_io
    return profiles_io.apply_column(machine, env, sels, mirror=mirror)


def reconcile_prs() -> int:
    """Reconcile the LOCAL machine's stale PR states against the provider (#1423).

    Delegates to :func:`data_local.reconcile_prs`. Each *remote* machine's PRs are
    reconciled on their own owning machine by :meth:`LiveLoader.reconcile_remote_prs`
    (a bounded, after-first-paint ``list --reconcile-prs`` over SSH, #2102).
    """
    return data_local.reconcile_prs()


def _reconcile_argv(source: "Source"):
    """The remote list argv for *source* with PR reconcile enabled (#2102).

    Mirrors ``source.argv`` but adds ``--reconcile-prs`` so the remote reconciles
    (and persists) its own PR states while listing. Honors the source's current
    ``use_classify`` so a remote that already fell back off ``--classify`` isn't
    handed it again.
    """
    return _argv_for(source.shell, source.alias, _project(),
                     classify=source.use_classify, reconcile=True)


def _resolve_local() -> tuple[str, str]:
    """(machine, env) of this host, using the registry display name when known.

    Falls back to ``data_local.LOCAL`` (hostname-based) if the local machine is
    not represented in ``machines.yaml`` -- or if config context is not yet
    resolvable at import time (e.g. before ``main()`` establishes the active
    project, as when the test suite imports this module).
    """
    try:
        for s in _build_sources():
            if s.local:
                return s.key
    except Exception:
        # Import-time config I/O must never hard-crash the import. In a real
        # picker run this module is imported after main() has established the
        # active project, so the try succeeds; this guard only covers early /
        # context-free imports (e.g. the test suite) where a hostname-based
        # local is the correct, safe fallback.
        pass
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


# On Windows, keep ssh children off the picker's console. A child ``ssh.exe``
# otherwise opens the shared CONIN$/CONOUT$ directly -- writing its errors over
# the TUI and, worse, calling SetConsoleMode which clears the
# ENABLE_VIRTUAL_TERMINAL_INPUT bit Textual set on that console. After that,
# arrow-key escape sequences decode as NUL (ctrl+@) and Up/Down stop working
# until the picker is relaunched, while single-byte keys ([, ], Tab, Enter,
# Esc) keep working. This bites hardest when the ssh *fails* (e.g. an
# unreachable remote), because the failing child mangles the console mode and
# never restores it. CREATE_NO_WINDOW gives the child its own (absent) console
# so it can't touch ours. stdin=DEVNULL additionally stops ssh from reading the
# operator's keystrokes.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(argv, timeout):
    kwargs = dict(
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        # DEVNULL gives ssh an empty stdin (instant EOF) so a background ssh
        # child can't read the operator's keystrokes out from under the TUI.
        stdin=subprocess.DEVNULL,
    )
    if os.name == "nt" and _CREATE_NO_WINDOW:
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    return subprocess.run(argv, **kwargs)


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


def _fetch(source: Source, runner=None, *, classify: bool = True, argv=None):
    """Run one source's list command and return normalized worktree records.

    Local sources load in-process. Remotes run over SSH, retrying without
    ``--classify`` when the remote agent-worktrees is too old to recognize it.

    ``runner`` runs the subprocess (default :func:`_run`); :class:`LiveLoader`
    passes its own tracked-and-killable runner so a picker exit can cancel any
    in-flight prefetch. ``argv`` overrides the source's default list argv (used
    by the PR-reconcile pass, #2102) without persisting to ``source.argv``.

    ``classify`` only affects the **local** source: ``False`` skips the expensive
    per-worktree git classification for a fast provisional listing (the loader's
    phase-1 fast pass). Remote sources always classify via their argv (the
    ``--classify`` flag), so this flag is a no-op for them.
    """
    runner = runner or _run
    if source.local:
        return data_local.load(source.machine, source.env, classify=classify)

    use_argv = argv if argv is not None else source.argv
    proc = runner(use_argv, source.timeout)
    if proc.returncode != 0 and _is_classify_unsupported(proc.stderr):
        # Older remote: drop --classify and retry (rows will lack canonical
        # state but still load).
        retry = [a.replace(" --classify", "") for a in use_argv]
        use_argv = retry
        # Persist the fallback only for the source's own default argv, not for a
        # transient override (e.g. the reconcile pass).
        if argv is None:
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
        # Keys with a silent background refresh in flight (#1421) -- a per-source
        # guard so a slow machine isn't re-hit every poll interval.
        self._refreshing: set = set()
        # Remote sources whose PRs have already been reconciled this session
        # (#2102) -- the remote-over-SSH reconcile runs once per source, as each
        # becomes ready, not on every poll.
        self._pr_reconciled_keys: set = set()
        # Per-source generation: bumped by reload() so an in-flight silent
        # repoll that started earlier never commits stale rows over a newer
        # intentional reload (#1421, ordering fix).
        self._gen: dict = {}
        for s in all_sources:
            self._state[s.key] = "loading" if s.ready else "failed"
            self._records[s.key] = []
            self._gen[s.key] = 0

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
                    # Invalidate any in-flight silent repoll of this source so
                    # its (older) rows can't land after this reload (#1421).
                    self._gen[s.key] = self._gen.get(s.key, 0) + 1
                threading.Thread(
                    target=self._load_one, args=(s,),
                    name=f"reload-{s.machine}-{s.env}", daemon=True,
                ).start()
                return True
        return False

    def repoll_silent(self, keys=None):
        """Background-refresh currently-ready sources *in place* (#1421).

        Unlike :meth:`reload`, this never flips a source back to ``loading`` --
        no spinner, no transient empty list. Each ready source re-fetches on a
        daemon thread and swaps its records only on success; a failed refresh
        silently keeps the last-good rows. A per-source in-flight guard means a
        slow machine is skipped (not re-hit) on the next poll. ``keys`` bounds
        the pass to a set of ``(machine, env)`` -- the picker passes only the
        machines currently in view so a specific-machine tab never fans out to
        the whole fleet. No-op once the loader is cancelled (picker teardown).

        Returns the number of sources a refresh was actually started for.
        """
        if self._cancelled.is_set():
            return 0
        started = 0
        with self._lock:
            for s in self._sources:
                if keys is not None and s.key not in keys:
                    continue
                if self._state.get(s.key) != "ready":
                    continue
                if s.key in self._refreshing:
                    continue
                self._refreshing.add(s.key)
                gen = self._gen.get(s.key, 0)
                started += 1
                threading.Thread(
                    target=self._refresh_one, args=(s, gen),
                    name=f"repoll-{s.machine}-{s.env}", daemon=True,
                ).start()
        return started

    def _refresh_one(self, source: Source, gen: int):
        """Silent re-fetch for :meth:`repoll_silent`: swap records on success,
        keep last-good on failure, and always clear the in-flight guard.

        ``gen`` is the source's generation captured when the refresh started;
        the fetched rows are committed only if the generation is unchanged --
        i.e. no :meth:`reload` superseded this refresh while it ran (#1421)."""
        try:
            if self._cancelled.is_set():
                return
            recs = _fetch(source, runner=self._spawn)
        except Exception:
            return  # keep last-good records; no state flip
        else:
            with self._lock:
                # Commit only when still ready, not cancelled, and not
                # superseded by a newer reload() (generation unchanged).
                if (not self._cancelled.is_set()
                        and self._state.get(source.key) == "ready"
                        and self._gen.get(source.key, 0) == gen):
                    self._records[source.key] = recs
        finally:
            with self._lock:
                self._refreshing.discard(source.key)

    def reconcile_remote_prs(self, keys=None):
        """Reconcile each ready REMOTE machine's own PR state, on that machine (#2102).

        For every ready remote source (local PRs reconcile via the engine's
        #1423 path), run ``list --reconcile-prs`` over SSH once -- the remote
        corrects and persists its stale PR states -- and swap the corrected rows
        in place, exactly like :meth:`repoll_silent` (no spinner, generation-
        checked, best-effort). Runs at most once per source per session, as each
        becomes ready, so a remote tab stops showing a merged-elsewhere worktree
        as having an open PR without ever blocking the first paint. ``keys``
        bounds the pass to the machines currently in view. No-op once cancelled.

        Returns the number of sources a reconcile was actually started for.
        """
        if self._cancelled.is_set():
            return 0
        started = 0
        with self._lock:
            for s in self._sources:
                if s.local:
                    continue
                if keys is not None and s.key not in keys:
                    continue
                if self._state.get(s.key) != "ready":
                    continue
                if s.key in self._pr_reconciled_keys or s.key in self._refreshing:
                    continue
                self._pr_reconciled_keys.add(s.key)
                self._refreshing.add(s.key)
                gen = self._gen.get(s.key, 0)
                started += 1
                threading.Thread(
                    target=self._reconcile_one, args=(s, gen),
                    name=f"reconcile-{s.machine}-{s.env}", daemon=True,
                ).start()
        return started

    def _reconcile_one(self, source: Source, gen: int):
        """Silent remote PR-reconcile for :meth:`reconcile_remote_prs`: fetch with
        ``--reconcile-prs`` and swap records on success, keep last-good on
        failure, and always clear the in-flight guard.

        ``gen`` guards against committing over a newer :meth:`reload` (#1421).
        A failed reconcile leaves ``_pr_reconciled_keys`` set (best-effort, one
        attempt) so a persistently-unreachable remote isn't re-hit every poll.
        """
        try:
            if self._cancelled.is_set():
                return
            recs = _fetch(source, runner=self._spawn, argv=_reconcile_argv(source))
        except Exception:
            return  # keep last-good records; no state flip
        else:
            with self._lock:
                if (not self._cancelled.is_set()
                        and self._state.get(source.key) == "ready"
                        and self._gen.get(source.key, 0) == gen):
                    self._records[source.key] = recs
        finally:
            with self._lock:
                self._refreshing.discard(source.key)

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
            # CREATE_NEW_PROCESS_GROUP: killable as a group on cancel.
            # CREATE_NO_WINDOW: keep the ssh child off our console so a failing
            # ssh can't clear the console's VT-input mode and break arrow keys.
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | _CREATE_NO_WINDOW
            )
        proc = subprocess.Popen(argv, **kwargs)
        with self._procs_lock:
            self._procs.append(proc)
        # Close the cancel/spawn race: if cancel() ran between the top-of-method
        # check and the Popen above, it never saw this child. Re-check now that
        # it's registered and kill it so a late spawn can't orphan an ssh child.
        if self._cancelled.is_set():
            _kill_proc_tree(proc)
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
        if source.local:
            # Local tab: fast-then-fill so rows paint immediately (see below).
            self._load_local_two_phase(source)
            return
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

    def _load_local_two_phase(self, source: Source):
        """Fast-then-fill for the local tab.

        Phase 1 (``classify=False``) is cheap -- tracking + sessions + mux, no
        per-worktree git -- so the local rows paint at once (via ``derive``'s
        classification-absent heuristic: status + turns + PR) instead of blocking
        on git classification of every worktree, which can take seconds or stall.
        Phase 2 (``classify=True``) runs the full git classification and swaps
        the authoritative ``state`` in. A generation guard keeps a concurrent
        :meth:`reload` from being clobbered by this pass's phase 2 (#1421)."""
        gen = self._gen.get(source.key, 0)
        try:
            fast = _fetch(source, classify=False)
        except Exception as exc:
            with self._lock:
                self._state[source.key] = "failed"
                self._error[source.key] = str(exc).strip() or type(exc).__name__
            return
        with self._lock:
            if self._cancelled.is_set():
                return
            self._records[source.key] = fast
            self._state[source.key] = "ready"
        # Phase 2: authoritative git classification, swapped in on success.
        try:
            full = _fetch(source, classify=True)
        except Exception:
            return  # keep the honest phase-1 heuristic rows
        with self._lock:
            if (not self._cancelled.is_set()
                    and self._state.get(source.key) == "ready"
                    and self._gen.get(source.key, 0) == gen):
                self._records[source.key] = full

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
