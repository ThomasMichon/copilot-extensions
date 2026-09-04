"""Standalone Session Host process launcher + per-OS survival adapters.

The Session Host must **outlive the agent-bridge frontend**. Two seams:

* **Spawning the host so it survives the front** -- :func:`host_spawn_kwargs`
  returns the ``subprocess`` kwargs the frontend uses to launch the host
  *outside* its own teardown domain: on Windows ``CREATE_BREAKAWAY_FROM_JOB``
  (escaping the daemon's kill-on-close job, which now permits breakaway -- see
  ``winjob``); on POSIX ``start_new_session=True`` (own session, immune to the
  front's process-group teardown). The shared contained-test policy suppresses
  both forms so the repository test supervisor retains ownership.
* **The host hardening itself once running** -- :func:`apply_host_survival`
  re-asserts session/job isolation from inside the host process (idempotent),
  and arms the host's *own* kill-on-close job on Windows so the child dies with
  the **host**, not the front.

:func:`run_host` is the entry point: apply survival, spawn the child, serve the
reattachable endpoint, and write a state file (``pid``/``child_pid``/``port``)
the frontend's host index reads. Runnable as ``python -m agent_bridge.session_host``.

This launcher takes an explicit child command; wiring it to agent-bridge's
worktree-resolve/``spawn_local`` path and the frontend reattach index is Phase 2.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_procutil import (
    contained_test_mode,
    no_window_flags,
    windowless_daemon_kwargs,
)

from .. import winjob
from . import protocol as proto
from .host import SessionHost
from .osutil import child_preexec

_ACP_STDIO_LIMIT_BYTES = 64 * 1024 * 1024

# Env var carrying the connect-auth nonce to the host process (kept off the
# command line so it does not leak to ps/Task Manager). Stripped before the
# copilot child is spawned so the child never inherits it.
_NONCE_ENV = "AGENT_BRIDGE_SESSION_HOST_NONCE"


def host_spawn_kwargs() -> dict[str, Any]:
    """``subprocess`` kwargs for the FRONTEND to spawn the host so it survives.

    On Windows the host must break away from the daemon's kill-on-close job
    (permitted because that job now carries ``JOB_OBJECT_LIMIT_BREAKAWAY_OK``);
    on POSIX it gets its own session.
    """
    return windowless_daemon_kwargs(breakaway=True)


@dataclass
class HostHandle:
    """A launched Session Host process + how to reach it."""

    host_pid: int
    child_pid: int
    port: int
    state_file: str
    proc: subprocess.Popen
    protocol_version: int = proto.PROTOCOL_VERSION


def launch_session_host(
    child_argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    state_dir: str | os.PathLike[str] | None = None,
    ready_timeout: float = 90.0,
    nonce: str = "",
    session_id: str = "",
    host_version: str = "",
    unexpected_reap_seconds: float = 60.0,
    active_reap_seconds: float = 0.0,
) -> HostHandle:
    """Spawn a **survivable** Session Host process that owns ``child_argv``.

    The host is launched with :func:`host_spawn_kwargs` so it outlives this
    frontend (Windows job-breakaway / POSIX new-session). It serves a loopback
    reattach endpoint and writes a ``pid``/``child_pid``/``port`` state file,
    which this call waits for. The child inherits ``env`` (so worktree/plan env
    vars reach copilot). Raises ``TimeoutError`` if the host never reports ready.

    ``nonce`` (optional) is the connect-auth token: it is handed to the host
    process **via its environment** (not the command line, so it does not leak
    to ``ps``/Task Manager) and the host requires a matching nonce on ATTACH.
    The copilot child never sees it -- ``run_host`` strips it before spawn.
    """
    sd = Path(state_dir) if state_dir else Path(tempfile.mkdtemp(prefix="agbridge-host-"))
    sd.mkdir(parents=True, exist_ok=True)
    state_file = sd / f"host-{os.getpid()}-{int(time.time()*1000)}.json"

    host_argv = [sys.executable, "-m", "agent_bridge.session_host",
                 "--port", "0", "--state-file", str(state_file)]
    if session_id:
        host_argv += ["--session-id", session_id]
    if host_version:
        host_argv += ["--host-version", host_version]
    if cwd:
        host_argv += ["--cwd", cwd]
    host_argv += ["--unexpected-reap-seconds", str(unexpected_reap_seconds)]
    host_argv += ["--active-reap-seconds", str(active_reap_seconds)]
    host_argv += ["--", *child_argv]

    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    if nonce:
        child_env[_NONCE_ENV] = nonce

    proc = subprocess.Popen(
        host_argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=child_env,
        cwd=cwd or None,
        **host_spawn_kwargs(),
    )

    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"session host exited early (code={proc.returncode}) before ready"
            )
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}
            if data.get("port") and data.get("child_pid"):
                return HostHandle(
                    host_pid=int(data["pid"]),
                    child_pid=int(data["child_pid"]),
                    port=int(data["port"]),
                    state_file=str(state_file),
                    proc=proc,
                    protocol_version=int(data.get("protocol_version",
                                                  proto.PROTOCOL_VERSION)),
                )
        time.sleep(0.05)

    raise TimeoutError(f"session host did not become ready within {ready_timeout}s")


def apply_host_survival() -> None:
    """Harden the *current* (host) process against the front's teardown.

    Idempotent and best-effort. POSIX: become a session leader if not already
    (immune to the front's process-group signals). Windows: arm the host's own
    kill-on-close job so the child dies with the host (the host itself already
    broke away from the front's job at spawn time).
    """
    if contained_test_mode():
        return
    if sys.platform == "win32":
        winjob.setup_kill_on_close_job(allow_breakaway=True)
    else:
        try:
            os.setsid()
        except OSError:
            # Already a session/group leader (spawned with start_new_session).
            pass


def _resolve_child_exe(argv: list[str], path: str | None) -> list[str]:
    """Resolve ``argv[0]`` against ``path`` (the *child's* PATH).

    ``asyncio.create_subprocess_exec`` resolves a bare ``argv[0]`` against the
    **host** process's PATH, not the ``env`` we hand the child. Under a minimal
    systemd ``--user`` service PATH (which omits ``~/.local/bin``), a bare
    ``copilot`` is not found, the Session Host exits code=1, and every ACP
    session fails. Mirror the resolution the direct-launch path already does
    (``transport.py``: ``shutil.which`` with the target PATH). Returns a new
    argv with an absolute ``argv[0]`` when resolvable; otherwise the argv
    unchanged (let the OS raise its usual error).
    """
    if not argv:
        return argv
    resolved = shutil.which(argv[0], path=path)
    if not resolved:
        return list(argv)
    return [resolved, *argv[1:]]


async def _spawn_child(
    argv: list[str], cwd: str | None, env: dict[str, str] | None,
) -> asyncio.subprocess.Process:
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    # Host-only payload and attach context must be re-established by the
    # Copilot child's own plugin hooks, never inherited from this launcher.
    child_env.pop(_NONCE_ENV, None)
    child_env.pop("COPILOT_PLUGIN_ROOT", None)
    # POSIX/Linux: arm PR_SET_PDEATHSIG so copilot dies with the host even on a
    # hard host kill -- the Linux counterpart to the Windows kill-on-close job,
    # so a remote (mesh/CodeSpace) far side never orphans copilot. None (default)
    # on Windows, where preexec_fn is unsupported.
    preexec = child_preexec()
    spawn_argv = _resolve_child_exe(argv, child_env.get("PATH"))
    return await asyncio.create_subprocess_exec(
        *spawn_argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=cwd or None,
        env=child_env,
        limit=_ACP_STDIO_LIMIT_BYTES,
        preexec_fn=preexec,
        creationflags=no_window_flags(),
    )


async def run_host(
    child_argv: list[str],
    *,
    port: int = 0,
    state_file: str | os.PathLike[str] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    ready: asyncio.Event | None = None,
    nonce: str = "",
    session_id: str = "",
    host_version: str = "",
    reverse_forwards: list[str] | None = None,
    unexpected_reap_seconds: float = 60.0,
    active_reap_seconds: float = 0.0,
) -> None:
    """Spawn the child, serve the reattachable endpoint, run until closed.

    ``nonce`` (or, if empty, the ``AGENT_BRIDGE_SESSION_HOST_NONCE`` env) arms
    connect-auth: the host then refuses any front that does not present it.
    ``unexpected_reap_seconds`` bounds how long an idle, front-less child lingers
    after an *unexpected* disconnect before the host self-reaps it (0 disables).
    ``active_reap_seconds`` bounds how long a still-active (mid-turn) front-less
    child is held after an unexpected disconnect before the host lets it go (0
    disables -- an active child then lives until its own stop).
    """
    apply_host_survival()
    nonce = nonce or os.environ.get(_NONCE_ENV, "")
    child = await _spawn_child(child_argv, cwd, env)
    state_path = Path(state_file) if state_file is not None else None
    state: dict[str, Any] = {}

    def _publish_child_exit(exit_code: int) -> None:
        if state_path is None or not state:
            return
        state["state"] = "child_exited"
        state["child_exit_code"] = exit_code
        state["child_exited_at"] = time.time()
        _write_host_state(state_path, state)

    host = SessionHost(child, nonce=nonce,
                       unexpected_reap_seconds=unexpected_reap_seconds,
                       active_reap_seconds=active_reap_seconds,
                       on_child_exit=_publish_child_exit)
    bound_port = await host.serve(port=port)
    state.update({
        "version": 2,
        "session_id": session_id,
        "pid": os.getpid(),
        "host_pid": os.getpid(),
        "child_pid": child.pid,
        "port": bound_port,
        "protocol_version": proto.PROTOCOL_VERSION,
        "host_version": host_version,
        "nonce": nonce,
        "created_at": time.time(),
        "state": "running",
        "child_executable": child_argv[0] if child_argv else "",
        "cwd": cwd or "",
        "reverse_forwards": list(reverse_forwards or []),
        "boot_id": _boot_id(),
        "host_start_ticks": _process_start_ticks(os.getpid()),
        "child_start_ticks": _process_start_ticks(child.pid),
    })
    if state_path is not None:
        _write_host_state(state_path, state)
        if not host.child_alive:
            exit_code = host.child_exit_code
            if exit_code is None:
                exit_code = child.returncode
            if exit_code is not None:
                _publish_child_exit(exit_code)
    if ready is not None:
        ready.set()
    try:
        await host.serve_forever()
    finally:
        await host.close()
        # Reap the child within the loop so its subprocess transport is torn
        # down cleanly (avoids proactor "Event loop is closed" warnings on
        # Windows). The child dies with the host by design.
        if child.returncode is None:
            try:
                child.kill()
            except ProcessLookupError:
                pass
            try:
                await child.wait()
            except ProcessLookupError:
                pass


def _boot_id() -> str:
    if sys.platform.startswith("linux"):
        try:
            return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            pass
    return ""


def _process_start_ticks(pid: int) -> str:
    if sys.platform.startswith("linux") and pid > 1:
        try:
            return Path(f"/proc/{pid}/stat").read_text().split()[21]
        except (OSError, IndexError):
            pass
    return ""


def _write_host_state(
    path: Path,
    payload: dict,
) -> bool:
    """Atomically publish one mode-0600 Session Host authority record."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
        stat = path.parent.stat()
        if stat.st_uid != os.geteuid() or stat.st_mode & 0o777 != 0o700:
            raise PermissionError(
                f"unsafe Session Host catalogue directory: {path.parent}"
            )
    fd, temp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        if hasattr(os, "fchmod"):
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temp, path)
        return True
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m agent_bridge.session_host",
        description="Standalone Session Host: own a Copilot --acp child, serve reattach.",
    )
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--state-file", default=None)
    ap.add_argument("--session-id", default="")
    ap.add_argument("--host-version", default="")
    ap.add_argument("--reverse-forward", action="append", default=[])
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--unexpected-reap-seconds", type=float, default=60.0)
    ap.add_argument("--active-reap-seconds", type=float, default=0.0)
    ap.add_argument("child", nargs=argparse.REMAINDER,
                    help="child command after `--` (e.g. -- copilot --acp --stdio)")
    args = ap.parse_args(argv)

    child_argv = args.child
    if child_argv and child_argv[0] == "--":
        child_argv = child_argv[1:]
    if not child_argv:
        ap.error("a child command is required after `--`")

    try:
        asyncio.run(run_host(
            child_argv,
            port=args.port,
            state_file=args.state_file,
            cwd=args.cwd,
            session_id=args.session_id,
            host_version=args.host_version,
            reverse_forwards=args.reverse_forward,
            unexpected_reap_seconds=args.unexpected_reap_seconds,
            active_reap_seconds=args.active_reap_seconds,
        ))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
