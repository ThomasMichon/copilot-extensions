"""Process- and lease-oriented helpers for the resident mux-daemon."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from agent_procutil import windowless_daemon_kwargs, windowless_python, windowless_python_env
from work_coalescing_singleton import client as wcs_client

from .mux_mapping_registry import _try_lock_file_once, _unlock_file

_SESSION_CREDENTIAL_ENV_KEYS = {"GH_TOKEN", "GITHUB_TOKEN", "AGENT_WORKTREES_AHP_AUTH_TOKEN"}


def daemon_is_live(data: dict | None, *, endpoint_from_rendezvous) -> bool:
    """Prove liveness by actually reaching the endpoint, not just trusting
    the lock file's presence."""
    endpoint = endpoint_from_rendezvous(data)
    if endpoint is None:
        return False
    host, port, token = endpoint
    client_id = wcs_client.new_client_id()
    try:
        wcs_client.subscribe(host, port, token, client_id, timeout=2.0)
    except wcs_client.DaemonUnavailable:
        return False
    finally:
        wcs_client.release(host, port, token, client_id, timeout=2.0)
    return True


def spawn_lock_path(root: Path) -> Path:
    return root / "mux-daemon.spawn.lock"


def acquire_daemon_lease(root: Path):
    """Attempt the daemon's single-instance lease (non-blocking)."""
    root.mkdir(parents=True, exist_ok=True)
    fh = open(spawn_lock_path(root), "a+b")
    if _try_lock_file_once(fh):
        return fh
    fh.close()
    return None


def release_daemon_lease(fh) -> None:
    try:
        _unlock_file(fh)
    except OSError:
        pass
    fh.close()


def scrub_session_credentials(env: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in env.items()
        if key.upper() not in _SESSION_CREDENTIAL_ENV_KEYS
    }


def direct_daemon_python(executable: str) -> tuple[str, dict[str, str]]:
    """Resolve direct console Python while preserving a Windows venv's imports.

    A venv redirector's PID is not the daemon PID. Do not silently retain
    that launch shape when the shared helper cannot select the real base
    interpreter: routing and generation ownership require the returned PID
    to belong to the runtime itself. Recurring mux children need a console
    root under windowless_daemon_kwargs, not a GUI-subsystem interpreter.
    """
    python = windowless_python(executable)
    extra_env = windowless_python_env(executable)
    config = os.path.join(os.path.dirname(os.path.dirname(executable)), "pyvenv.cfg")
    if os.name == "nt" and os.path.isfile(config) and not extra_env:
        raise RuntimeError(
            "Cannot start mux daemon directly: the Windows venv's base "
            "pythonw.exe is unavailable; repair the Python installation."
        )
    if os.name == "nt" and os.path.basename(python).lower() == "pythonw.exe":
        python = os.path.join(os.path.dirname(python), "python.exe")
        if not os.path.isfile(python):
            raise RuntimeError(
                "Cannot start mux daemon directly: the base python.exe "
                "is unavailable; repair the Python installation."
            )
    return python, extra_env


def spawn_detached(argv: list[str]) -> bool:
    """Spawn a survivable daemon; log expected launch failures and return False."""
    try:
        python, extra_env = direct_daemon_python(argv[0])
        env = scrub_session_credentials(dict(os.environ))
        env.update(extra_env)
        kwargs: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env": env,
        }
        kwargs.update(windowless_daemon_kwargs(breakaway=True))
        subprocess.Popen([python, *argv[1:]], **kwargs)  # noqa: S603 - trusted argv
        return True
    except (OSError, RuntimeError, ValueError) as exc:
        logging.getLogger(__name__).error("Mux daemon launch failed: %s", exc)
        return False


def detached_child_argv(root: Path | None = None) -> list[str]:
    argv = [sys.executable, "-m", "worktree_manager", "mux-daemon", "run"]
    if root is not None:
        argv.append(f"--root={root}")
    return argv
