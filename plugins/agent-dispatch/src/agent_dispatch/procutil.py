"""Process-spawn helpers shared across the worker-spawn backends.

The coordinator / supervisor runs windowless -- under the installed service
(a hidden Windows Scheduled Task launched via ``conhost --headless``) or inside
the agent-bridge daemon. When such a windowless parent shells out to a *console*
launcher -- the ``agent-bridge`` / ``agent-worktrees`` ``.cmd`` binstubs (which
re-exec ``python.exe``) or ``ssh.exe`` -- Windows allocates a fresh console
window for the child, which **flashes on screen once per worker spawn**.

:func:`run_background_capture` launches a short-lived process tree with captured
stdio. On Windows its root must be a console-subsystem executable and receives
``CREATE_NO_WINDOW``. That mode keeps descendant console programs on the same
windowless process tree instead of letting each invoke Windows Default Terminal.
Do not substitute ``pythonw.exe``: Windows ignores ``CREATE_NO_WINDOW`` for GUI
applications, and a detached GUI parent leaves console descendants free to
allocate their own consoles.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from .install_paths import install_dir

# Re-exported so existing callers keep using ``from .procutil import
# no_window_kwargs`` while the implementation is single-sourced in the shared
# ``agent_procutil`` lib.
from agent_procutil import (
    detached_kwargs,
    no_window_flags,
    no_window_kwargs,
    windowless_daemon_kwargs,
    windowless_python,
    windowless_python_env,
)

__all__ = [
    "agent_bridge_launch_prefix",
    "agent_worktrees_environment",
    "agent_worktrees_launch_prefix",
    "detached_kwargs",
    "no_window_flags",
    "no_window_kwargs",
    "powershell_host",
    "relocate_off_payload",
    "resolve_own_runtime_python",
    "resolve_runtime_python",
    "run_agent_worktrees_capture",
    "run_background_capture",
    "run_ssh_capture",
    "run_ssh_command",
    "ssh_subprocess_kwargs",
    "terminate_process_tree",
    "terminate_ssh_process_tree",
    "runtime_root",
    "windowless_daemon_kwargs",
    "windowless_python",
    "windowless_python_env",
]

#: Env vars scrubbed before spawning the ``agent-worktrees`` CLI as a plain
#: sibling binstub (not a full peer-plugin context handoff -- see
#: ``peer_launch.peer_environment`` for that case). Mirrors the precedent in
#: agent-bridge's ``worktree_head.py`` (``VIRTUAL_ENV``/``PYTHONHOME``/
#: ``__PYVENV_LAUNCHER__``/``PYTHONPATH``, which otherwise trip an ``_sre``
#: module mismatch in a uv-managed child interpreter), plus
#: ``COPILOT_PLUGIN_ROOT``: agent-dispatch commonly runs as a long-lived daemon
#: started with that var set to its OWN payload dir, and letting it leak into
#: a spawned ``agent-worktrees`` process trips agent-worktrees's own
#: payload-context guard (shim vs. inherited context mismatch) -- the same
#: class of bug tracked for agent-bridge's peer-binstub delegation in #2268.
_AGENT_WORKTREES_ENV_SCRUB = frozenset({
    "COPILOT_PLUGIN_ROOT",
    "PYTHONHOME",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "__PYVENV_LAUNCHER__",
})

#: Memoized result of :func:`powershell_host`'s ``PATH`` resolution.
_POWERSHELL_HOST: str | None = None


def powershell_host() -> str:
    """Prefer ``pwsh`` (PowerShell 7); fall back to legacy Windows PowerShell
    5.1 only when ``pwsh`` isn't on ``PATH``.

    The Windows process-inventory enumeration in ``reap.py`` and
    ``supervisor_processes.py`` hardcoded ``"powershell"``/``"powershell.exe"``
    directly, sending every invocation through the legacy 5.1 host even on a
    machine where ``pwsh`` is installed and preferred.

    Memoized: some callers (e.g. a tight process-enumeration poll loop) invoke
    this on every cycle, and an unmemoized ``shutil.which`` PATH scan on each
    call would add measurable per-tick overhead. The resolved host cannot
    change within a process's lifetime once ``PATH`` is read at import/first
    use, so a single resolution is safe to reuse.
    """
    global _POWERSHELL_HOST
    if _POWERSHELL_HOST is None:
        _POWERSHELL_HOST = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    return _POWERSHELL_HOST


def agent_worktrees_environment() -> dict[str, str]:
    """Ambient environment, minus the vars unsafe to leak into a spawned
    ``agent-worktrees`` process (see :data:`_AGENT_WORKTREES_ENV_SCRUB`).

    Every direct ``subprocess`` spawn of the ``agent-worktrees`` CLI should pass
    ``env=agent_worktrees_environment()`` rather than inheriting the parent's
    environment verbatim (the default when no ``env=`` is given at all).
    """
    return {
        key: value
        for key, value in os.environ.items()
        if key not in _AGENT_WORKTREES_ENV_SCRUB
    }

#: Slot-interpreter subpaths, POSIX then Windows (matches
#: ``versioned_runtime.SLOT_PYTHON_SUBPATHS`` / ``resolve-runtime.ps1``).
_SLOT_PYTHON_SUBPATHS = ("bin/python", "Scripts/python.exe")


def _slot_python(root: Path, version: str) -> Path | None:
    """The interpreter inside ``<root>/versions/<version>``, or ``None``."""
    if not version:
        return None
    vdir = root / "versions" / version
    for sub in _SLOT_PYTHON_SUBPATHS:
        p = vdir / sub
        if p.is_file():
            return p
    return None


def _read_marker(root: Path, name: str) -> str | None:
    """Read a plain-text marker file (``current-version`` / ``last-known-good``)."""
    try:
        return (root / name).read_text(encoding="utf-8").strip() or None
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError -- a torn/binary-corrupt marker
        # write must fail safe (no marker) exactly like a missing file.
        return None


def _version_sort_key(version: str) -> str:
    """Version-aware sort key: zero-pad each numeric run so ``dev185 > dev50``
    (not lexicographic). Matches the shell resolver ``resolve-runtime.ps1`` (the
    resolver the binstubs use), which zero-pads numeric runs -- deliberately
    format-agnostic for the ``X.Y.Z-devN`` slot names here (not the
    ``packaging.version``/PEP 440 ordering ``versioned_runtime._version_key``
    uses). Only the tier-3 first-run fallback consults this ordering."""
    return re.sub(r"\d+", lambda m: m.group().zfill(10), version)


def _list_versions(root: Path) -> list[str]:
    """Installed version slot names, sorted oldest -> newest (newest last)."""
    try:
        names = [d.name for d in (root / "versions").iterdir() if d.is_dir()]
    except OSError:
        return []
    return sorted(names, key=_version_sort_key)


def resolve_runtime_python(root: Path) -> Path | None:
    """Canonically resolve a sibling plugin runtime's interpreter under ``root``.

    The **standardized spawn flow**: resolve a versioned-runtime interpreter the
    same way the binstubs, hooks, and service launchers do (``resolve-runtime.ps1``
    / ``versioned_runtime.resolve_python``) instead of hard-coding a ``venv`` path.
    ``root`` is a runtime root such as ``~/.agent-bridge`` or ``~/.agent-worktrees``.

    Three tiers, matching the canonical resolver:

    1. the ``current-version`` marker (source of truth, written atomically);
    2. ``last-known-good`` when the marker is missing/unresolvable;
    3. the newest **complete** installed slot, then any newest slot.

    Never resolves through a ``venv``/``.venv`` link (a reparse point Windows'
    RedirectionGuard blocks) and **never** falls back to a PATH python -- returns
    ``None`` when no runtime is installed so the caller degrades deliberately.
    """
    p = _slot_python(root, _read_marker(root, "current-version") or "")
    if p is not None:
        return p
    p = _slot_python(root, _read_marker(root, "last-known-good") or "")
    if p is not None:
        return p
    versions = _list_versions(root)  # newest last
    for ver in reversed(versions):
        if (root / "versions" / ver / ".install-complete.json").is_file():
            p = _slot_python(root, ver)
            if p is not None:
                return p
    for ver in reversed(versions):
        p = _slot_python(root, ver)
        if p is not None:
            return p
    return None


def resolve_own_runtime_python() -> str:
    """Canonically resolve **this plugin's own** current-version interpreter.

    A self-relaunch/detached-spawn site (a daemon spawning a sibling daemon, a
    supervisor spawning a registration child, a lazy-start helper spawning a
    coordinator) must always target the SAME slot the binstubs/installer would
    pick today -- never whatever interpreter happened to be running the
    *current* process. Trusting ``sys.executable`` for that decision is a
    footgun: a legacy pre-versioned-runtime code path, a stale cached path, or
    simply invoking the CLI directly via a non-canonical interpreter during
    debugging can all make ``sys.executable`` diverge from the installed
    current-version slot -- and because a detached child inherits whatever its
    parent resolved, that divergence then silently propagates and compounds
    down an entire spawn tree (observed in production: a live coordinator and
    supervisor, each correctly resolved, both parented a full duplicate
    tree -- coordinator, emitters, and all -- running the system Python
    install instead of the versioned slot, because one spawn site fell back to
    ``sys.executable`` after a legacy path check failed).

    Delegates to :func:`resolve_runtime_python` against this plugin's own
    ``~/.agent-dispatch`` root (the same three-tier resolution the binstubs and
    service launchers use), falling back to ``sys.executable`` **only** when
    that resolution finds no installed runtime at all (e.g. a dev/test
    environment with no real install) -- so this degrades exactly like
    ``resolve_runtime_python`` for a normal sibling-plugin lookup, it simply
    never returns ``None`` to a caller that needs *some* interpreter to spawn.
    """
    from .runtime_version import install_dir

    resolved = resolve_runtime_python(install_dir())
    return str(resolved) if resolved is not None else sys.executable


def agent_worktrees_launch_prefix() -> list[str] | None:
    """Resolve ``agent-worktrees`` without a Windows shell shim.

    The installed runtime interpreter is authoritative and bypasses the
    ``.cmd``/``.bat`` dispatch that would otherwise involve ``cmd.exe``. POSIX
    may fall back to its executable shim because it is a plain exec script.
    Windows deliberately has no PATH fallback.
    """
    return _sibling_runtime_launch_prefix(
        "agent-worktrees", "agent_worktrees", "agent-worktrees"
    )


def agent_bridge_launch_prefix() -> list[str] | None:
    """Resolve ``agent-bridge`` without a Windows shell shim."""
    return _sibling_runtime_launch_prefix(
        "agent-bridge", "agent_bridge", "agent-bridge"
    )


def agent_pull_requests_launch_prefix() -> list[str] | None:
    """Resolve ``agent-pull-requests`` without ambient ``PATH`` lookup.

    Required for any cross-plugin invocation (e.g. the pr-watch-daemon
    delegation in :mod:`agent_dispatch.execution_cli`): a payload-only
    orchestrator resolves a sibling command only inside its own
    provenance-checked installation (``~/.agent-pull-requests``'s own
    versioned runtime slot), never via ``shutil.which`` -- an ambient
    ``PATH`` lookup could select an unrelated or attacker-controlled
    executable ahead of the installed dependency (see
    ``docs/patterns/README.md``'s *Cross-plugin payload argv remains data*).
    """
    return _sibling_runtime_launch_prefix(
        "agent-pull-requests", "agent_pull_requests", "agent-pull-requests"
    )


def _sibling_runtime_launch_prefix(
    plugin_id: str, module: str, path_command: str
) -> list[str] | None:
    """Resolve a sibling module from the active installation mode."""
    explicit_context = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    if explicit_context:
        # Validate again at execution, not when a caller constructs its argv.
        # Only this native child boundary may rebind context/environment.
        from .peer_launch import launch_prefix

        return launch_prefix("agent-dispatch", install_dir(), explicit_context, plugin_id)
    root = Path.home() / f".{plugin_id}"
    py = resolve_runtime_python(root)
    if py is not None:
        return [str(py), "-m", module]
    if os.name != "nt":
        exe = shutil.which(path_command)
        if exe:
            return [exe]
    return None


def run_agent_worktrees_capture(
    *args: str, timeout: float
) -> subprocess.CompletedProcess[str] | None:
    """Run a captured ``agent-worktrees`` probe without Windows Default Terminal."""
    prefix = agent_worktrees_launch_prefix()
    if prefix is None:
        return None
    return run_background_capture(
        [*prefix, *args], timeout=timeout, env=agent_worktrees_environment()
    )


def run_background_capture(
    argv: Sequence[str | os.PathLike[str]],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Run a short-lived captured process tree without a headed Windows console.

    ``CREATE_NO_WINDOW`` is intentionally applied to the console-subsystem root,
    not to ``pythonw.exe`` and not combined with ``DETACHED_PROCESS``. This keeps
    later console descendants (for example ``git.exe``) from allocating a new
    Default Terminal console while preserving stdout/stderr pipes.

    Uses ``Popen`` + :func:`terminate_process_tree` rather than a bare
    ``subprocess.run(timeout=)``: on Windows the latter's ``TimeoutExpired``
    handling kills only the immediate child, but a venv ``python.exe`` launcher
    re-execs the base interpreter as a NEW child process (no true ``exec`` on
    Windows) -- so a bare timeout kill leaks that re-exec'd grandchild (and its
    own ``conhost.exe``) running indefinitely. This was a real, observed leak:
    a periodic caller (e.g. a GC/reap loop) whose probe stalls past its timeout
    accumulates one orphaned process pair per cycle, unbounded.

    ``env`` defaults to ``None`` (inherit the ambient environment verbatim, the
    prior behavior) -- a caller spawning a specific sibling plugin (e.g.
    :func:`run_agent_worktrees_capture`) should instead pass an explicitly
    scrubbed environment (:func:`agent_worktrees_environment`).
    """
    args = [os.fspath(arg) for arg in argv]
    try:
        spawn_time = time.monotonic()
        proc = subprocess.Popen(  # noqa: S603 -- fixed module argv
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            env=dict(env) if env is not None else None,
            **_process_tree_kwargs(),
        )
    except OSError:
        return None
    from .companion import process_start_token

    proc_pid = getattr(proc, "pid", None)
    start_token = None
    if isinstance(proc_pid, int):
        try:
            start_token = process_start_token(proc_pid)
        except Exception:
            # Best-effort only (matches spawn_factories.py's own precedent)
            # -- an indeterminate probe (e.g. its ps fallback failing) must
            # never abort the caller before it even gets to its own
            # communicate()/cleanup, leaving the just-spawned process
            # running with nothing left to reap it.
            start_token = None
    # `process_start_token`'s own `ps` fallback can block up to 5 seconds
    # (its own fixed timeout) when `/proc` is unavailable (e.g. macOS) --
    # without accounting for that against one wall-clock deadline,
    # `communicate(timeout=timeout)` below would start its own *fresh*
    # countdown afterward, letting a short-timeout caller run several
    # times longer than it declared. Charge the probe's own elapsed time
    # against the same budget.
    remaining = max(0.0, timeout - (time.monotonic() - spawn_time))
    try:
        stdout, stderr = proc.communicate(timeout=remaining)
    except subprocess.TimeoutExpired:
        terminate_process_tree(proc, expected_start_token=start_token)
        return None
    except subprocess.SubprocessError:
        terminate_process_tree(proc, expected_start_token=start_token)
        return None
    return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)


def ssh_subprocess_kwargs() -> dict[str, object]:
    """Contain SSH and ProxyCommand descendants in a windowless process tree."""
    return _process_tree_kwargs()


def _process_tree_kwargs() -> dict[str, object]:
    """``Popen`` kwargs that make the whole descendant tree killable as one unit.

    Windows: ``CREATE_NO_WINDOW`` (no process-group semantics needed --
    :func:`terminate_process_tree` uses ``taskkill /T`` there, which walks the
    tree by parent-PID rather than by process group). POSIX: a new session so
    ``os.killpg`` can reap every descendant, not just the immediate child.
    """
    if sys.platform == "win32":
        return {"creationflags": no_window_flags()}
    return {"start_new_session": True}


def _signal_ssh_process(proc: subprocess.Popen[object], method: str) -> None:
    try:
        getattr(proc, method)()
    except OSError:
        pass


def _posix_process_group_alive(pgid: int) -> bool:
    """Best-effort POSIX check for whether any process still belongs to
    process-group ``pgid`` -- signal ``0`` only probes existence/permission,
    it never actually signals anything."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_process_tree(
    proc: subprocess.Popen[object],
    *,
    grace: float = 5.0,
    expected_start_token: str | None = None,
) -> None:
    """Terminate ``proc`` and every descendant it spawned, not just itself.

    On Windows, a plain ``proc.kill()`` (what a bare ``subprocess.run(timeout=)``
    does on ``TimeoutExpired``) only terminates the immediate child. A venv
    ``python.exe`` launcher is a console-subsystem stub that re-execs the base
    interpreter as a NEW child process (Windows has no true ``exec``) -- so
    killing the launcher leaves that re-exec'd grandchild running indefinitely,
    each with its own ``conhost.exe``. ``taskkill /T`` walks the process tree by
    parent PID and kills it as a unit, which reaps the grandchild too. POSIX
    uses the process-group kill this proc's session leader owns (see
    :func:`_process_tree_kwargs`).

    Never short-circuits on ``proc.poll() is not None`` (the immediate leader
    having already exited): a descendant that inherited the group/session but
    outlived its leader -- notably one still holding the leader's stdout/
    stderr pipe open, the exact case that leaves a caller's ``communicate()``
    blocked past its own timeout -- would otherwise be left running
    untouched. POSIX uses ``pid`` directly as the process-group id rather
    than ``os.getpgid(pid)``: ``start_new_session=True`` (see
    :func:`_process_tree_kwargs`) makes the spawned leader both its session
    *and* process-group leader, so by POSIX definition its pgid always
    equals its own pid -- a fact that holds even once the leader itself has
    fully exited and been reaped, whereas ``os.getpgid(pid)`` requires a
    still-live process with that exact pid to resolve and raises
    ``ProcessLookupError`` the instant it is gone.

    After signaling, POSIX actively polls for the whole *group* (every
    member, not just the leader ``proc`` itself) to disappear before
    considering ``grace`` elapsed and escalating to ``SIGKILL``: waiting
    only on ``proc`` -- already-exited or not -- returns as soon as the
    leader is reaped regardless of surviving descendants, which would
    silently skip the escalation this function's own tree-reaping contract
    promises. On Windows, ``taskkill /T`` is still attempted for the same
    already-exited-leader reason, though a fully leader-independent
    guarantee there needs a Job Object handle (tracked as a known residual
    gap, not silently claimed solved).

    ``expected_start_token``, when supplied by the caller (captured via
    ``companion.process_start_token(proc.pid)`` immediately after spawning,
    before any possibility of PID reuse), fences every signal below against
    a stale/reused PID: a PID-based kill (`os.killpg`/`taskkill /PID`) can
    never rule out the recorded PID having since been reassigned to an
    unrelated process -- especially likely here, since this function is
    only ever called well after spawn (on a timeout), giving the original
    process ample time to have already exited and its PID been recycled.
    A definitively *different* current token refuses to signal at all;
    an indeterminate token (unqueryable, or the process already gone)
    proceeds as before, since there is nothing to protect either way.
    """
    pid = getattr(proc, "pid", None)
    leader_already_exited = proc.poll() is not None
    if not isinstance(pid, int) or pid <= 0:
        if leader_already_exited:
            return
        _signal_ssh_process(proc, "terminate")
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            _signal_ssh_process(proc, "kill")
        return
    if expected_start_token is not None:
        from .companion import process_start_token

        try:
            current_token = process_start_token(pid)
        except Exception:
            # An indeterminate probe (e.g. `ps`'s own transient failure/
            # timeout via `_run_captured`, which can raise
            # `CompanionIndeterminate`) must never escape and abort
            # cleanup -- that would leave the timed-out process tree
            # running and mask the caller's own timeout result. Treat it
            # the same as an unqueryable/absent token: proceed with the
            # signal, since there is nothing to protect against either
            # way.
            current_token = None
        if current_token is not None and current_token != expected_start_token:
            # `pid` has been reused by an unrelated process since spawn --
            # refuse to signal it.
            return
    if sys.platform == "win32":
        try:
            subprocess.run(  # noqa: S603, S607
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=no_window_flags(),
            )
        except OSError:
            if not leader_already_exited:
                _signal_ssh_process(proc, "kill")
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            if not leader_already_exited:
                _signal_ssh_process(proc, "kill")
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        # No process remains in this group at all -- nothing to escalate.
        try:
            proc.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            pass
        return
    except OSError:
        if not leader_already_exited:
            _signal_ssh_process(proc, "terminate")
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        # Reap the leader itself (non-blocking) the instant it exits --
        # left unreaped, a zombie leader still counts as a live member of
        # its own process group (`killpg(pgid, 0)` keeps succeeding until
        # something actually `wait()`s it), so `_posix_process_group_alive`
        # would otherwise report the group "alive" for the full grace
        # period even when every real descendant already exited,
        # needlessly escalating to SIGKILL below.
        proc.poll()
        if not _posix_process_group_alive(pid):
            break
        time.sleep(0.05)
    if _posix_process_group_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            proc.poll()
            if not _posix_process_group_alive(pid):
                break
            time.sleep(0.05)
    try:
        proc.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        pass


def terminate_ssh_process_tree(
    proc: subprocess.Popen[object],
    *,
    grace: float = 5.0,
) -> None:
    """Terminate an SSH root and the ProxyCommand descendants it spawned.

    Thin, name-stable wrapper over :func:`terminate_process_tree` -- the two
    process trees (SSH/ProxyCommand vs. a captured background probe) need the
    identical kill strategy, but existing callers/tests reference this SSH
    name specifically.
    """
    terminate_process_tree(proc, grace=grace)


def run_ssh_capture(
    argv: Sequence[str | os.PathLike[str]],
    *,
    timeout: float,
    input: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Run a captured SSH process tree without visible ProxyCommand windows."""
    try:
        return run_ssh_command(argv, timeout=timeout, input=input)
    except (OSError, subprocess.SubprocessError):
        return None


def run_ssh_command(
    argv: Sequence[str | os.PathLike[str]],
    *,
    timeout: float | None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run SSH with captured text output and tree-safe timeout cleanup."""
    args = [os.fspath(arg) for arg in argv]
    proc = subprocess.Popen(  # noqa: S603 -- fixed SSH argv
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        text=True,
        **ssh_subprocess_kwargs(),
    )
    try:
        stdout, stderr = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        terminate_ssh_process_tree(proc)
        raise subprocess.TimeoutExpired(
            args,
            timeout,
            output=exc.output,
            stderr=exc.stderr,
        ) from exc
    except KeyboardInterrupt:
        terminate_ssh_process_tree(proc)
        raise
    return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)


def runtime_root() -> Path:
    """The agent-dispatch runtime root (``~/.agent-dispatch``) -- a stable dir that
    is **never** under the Copilot plugin payload. Safe as a daemon's working
    directory and as a spawn ``cwd``."""
    return install_dir()


def relocate_off_payload() -> None:
    """Move the current process's working directory to :func:`runtime_root`.

    A long-lived daemon (the coordinator / supervisor) is lazy-started from a
    session-start hook and inherits that session's CWD -- which is often the
    **plugin payload dir** (``~/.copilot/installed-plugins/.../agent-dispatch``).
    On Windows a live process's CWD **locks that directory tree**, so a payload
    CWD makes ``copilot plugin update`` fail with ``os error 32`` (the runtime
    can never be updated while the daemon it installed is running). Relocating to
    the runtime root at daemon startup guarantees no daemon ever holds the payload
    -- independent of how it was launched. Best-effort; never fatal.
    """
    try:
        root = runtime_root()
        root.mkdir(parents=True, exist_ok=True)
        os.chdir(root)
    except OSError:
        pass
