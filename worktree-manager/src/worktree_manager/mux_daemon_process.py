"""Process- and lease-oriented helpers for the resident mux-daemon."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agent_procutil import windowless_daemon_kwargs
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


def spawn_detached(argv: list[str]) -> bool:
    """Spawn a survivable background daemon. Best-effort; never raises."""
    env = scrub_session_credentials(dict(os.environ))
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
    }
    kwargs.update(windowless_daemon_kwargs(breakaway=True))
    try:
        subprocess.Popen(argv, **kwargs)  # noqa: S603 - trusted argv
        return True
    except Exception:
        return False


def detached_child_argv(root: Path | None = None) -> list[str]:
    argv = [sys.executable, "-m", "worktree_manager", "mux-daemon", "run"]
    if root is not None:
        argv.append(f"--root={root}")
    return argv
