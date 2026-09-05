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
from collections.abc import Sequence
from pathlib import Path

# Re-exported so existing callers keep using ``from .procutil import
# no_window_kwargs`` while the implementation is single-sourced in the shared
# ``agent_procutil`` lib.
from agent_procutil import (
    detached_kwargs,
    no_window_flags,
    no_window_kwargs,
    windowless_daemon_kwargs,
    windowless_python,
)

__all__ = [
    "agent_bridge_launch_prefix",
    "agent_worktrees_launch_prefix",
    "detached_kwargs",
    "no_window_flags",
    "no_window_kwargs",
    "relocate_off_payload",
    "resolve_runtime_python",
    "run_agent_worktrees_capture",
    "run_background_capture",
    "run_ssh_capture",
    "run_ssh_command",
    "ssh_subprocess_kwargs",
    "terminate_ssh_process_tree",
    "runtime_root",
    "windowless_daemon_kwargs",
    "windowless_python",
]

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
    except OSError:
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


def agent_worktrees_launch_prefix() -> list[str] | None:
    """Resolve ``agent-worktrees`` without a Windows shell shim.

    The installed runtime interpreter is authoritative and bypasses the
    ``.cmd``/``.bat`` dispatch that would otherwise involve ``cmd.exe``. POSIX
    may fall back to its executable shim because it is a plain exec script.
    Windows deliberately has no PATH fallback.
    """
    return _sibling_runtime_launch_prefix(
        ".agent-worktrees", "agent_worktrees", "agent-worktrees"
    )


def agent_bridge_launch_prefix() -> list[str] | None:
    """Resolve ``agent-bridge`` without a Windows shell shim."""
    return _sibling_runtime_launch_prefix(
        ".agent-bridge", "agent_bridge", "agent-bridge"
    )


def _sibling_runtime_launch_prefix(
    runtime_dir: str, module: str, path_command: str
) -> list[str] | None:
    """Resolve a sibling's module launcher, with a POSIX-only PATH fallback."""
    py = resolve_runtime_python(Path.home() / runtime_dir)
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
    return run_background_capture([*prefix, *args], timeout=timeout)


def run_background_capture(
    argv: Sequence[str | os.PathLike[str]], *, timeout: float
) -> subprocess.CompletedProcess[str] | None:
    """Run a short-lived captured process tree without a headed Windows console.

    ``CREATE_NO_WINDOW`` is intentionally applied to the console-subsystem root,
    not to ``pythonw.exe`` and not combined with ``DETACHED_PROCESS``. This keeps
    later console descendants (for example ``git.exe``) from allocating a new
    Default Terminal console while preserving stdout/stderr pipes and normal
    ``subprocess.run`` timeout handling. POSIX receives no creation flags, so its
    process behavior is unchanged.
    """
    creation_kwargs = no_window_kwargs() if os.name == "nt" else {}
    try:
        return subprocess.run(  # noqa: S603 -- fixed module argv
            [os.fspath(arg) for arg in argv],
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            **creation_kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def ssh_subprocess_kwargs() -> dict[str, object]:
    """Contain SSH and ProxyCommand descendants in a windowless process tree."""
    if sys.platform == "win32":
        return {"creationflags": no_window_flags()}
    return {"start_new_session": True}


def _signal_ssh_process(proc: subprocess.Popen[object], method: str) -> None:
    try:
        getattr(proc, method)()
    except OSError:
        pass


def terminate_ssh_process_tree(
    proc: subprocess.Popen[object],
    *,
    grace: float = 5.0,
) -> None:
    """Terminate an SSH root and the ProxyCommand descendants it spawned."""
    if proc.poll() is not None:
        return
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        _signal_ssh_process(proc, "terminate")
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            _signal_ssh_process(proc, "kill")
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
            _signal_ssh_process(proc, "kill")
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            _signal_ssh_process(proc, "kill")
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    if sys.platform != "win32":
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            _signal_ssh_process(proc, "kill")
    else:
        _signal_ssh_process(proc, "kill")
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


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
    return Path.home() / ".agent-dispatch"


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
