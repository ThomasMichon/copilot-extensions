"""Standalone Session Host process launcher + per-OS survival adapters.

The Session Host must **outlive the agent-bridge frontend**. Two seams:

* **Spawning the host so it survives the front** -- :func:`host_spawn_kwargs`
  returns the ``subprocess`` kwargs the frontend uses to launch the host
  *outside* its own teardown domain: on Windows ``CREATE_BREAKAWAY_FROM_JOB``
  (escaping the daemon's kill-on-close job, which now permits breakaway -- see
  ``winjob``); on POSIX ``start_new_session=True`` (own session, immune to the
  front's process-group teardown).
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
    if sys.platform == "win32":
        # CREATE_NO_WINDOW keeps it headless; breakaway escapes the front's job.
        return {"creationflags": 0x08000000 | winjob.CREATE_BREAKAWAY_FROM_JOB}
    return {"start_new_session": True}


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
    ready_timeout: float = 30.0,
    nonce: str = "",
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
    if cwd:
        host_argv += ["--cwd", cwd]
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
    # The connect-auth nonce is for the host process only -- never leak it into
    # the copilot child's environment.
    child_env.pop(_NONCE_ENV, None)
    if env:
        child_env.update(env)
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
) -> None:
    """Spawn the child, serve the reattachable endpoint, run until closed.

    ``nonce`` (or, if empty, the ``AGENT_BRIDGE_SESSION_HOST_NONCE`` env) arms
    connect-auth: the host then refuses any front that does not present it.
    """
    apply_host_survival()
    nonce = nonce or os.environ.get(_NONCE_ENV, "")
    child = await _spawn_child(child_argv, cwd, env)
    host = SessionHost(child, nonce=nonce)
    bound_port = await host.serve(port=port)
    if state_file is not None:
        Path(state_file).write_text(json.dumps({
            "pid": os.getpid(),
            "child_pid": child.pid,
            "port": bound_port,
            "protocol_version": proto.PROTOCOL_VERSION,
        }))
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m agent_bridge.session_host",
        description="Standalone Session Host: own a Copilot --acp child, serve reattach.",
    )
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--state-file", default=None)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("child", nargs=argparse.REMAINDER,
                    help="child command after `--` (e.g. -- copilot --acp --stdio)")
    args = ap.parse_args(argv)

    child_argv = args.child
    if child_argv and child_argv[0] == "--":
        child_argv = child_argv[1:]
    if not child_argv:
        ap.error("a child command is required after `--`")

    try:
        asyncio.run(run_host(child_argv, port=args.port, state_file=args.state_file,
                             cwd=args.cwd))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
