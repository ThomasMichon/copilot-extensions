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

:func:`run_host` owns the child and reattachable endpoint. Local ordinary
launches retain a copied core archive and use a physical base interpreter,
not the caller's venv. Caller-owned workspace and original-startup admission
remain outside the core.
"""

from __future__ import annotations

from agent_procutil.daemon_instances import (
    DaemonInstanceSpec, daemon_entrypoint, spawn_instance,
)

import argparse
import asyncio
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from agent_procutil import (
    contained_test_mode,
    no_window_flags,
    windowless_daemon_kwargs,
)

from .. import winjob
from ..session_preferences import execution_receipt
from . import protocol as proto
from .host import SessionHost
from .host_state import write_host_state as _write_host_state
from .osutil import child_preexec, process_start_time
from .startup_admission import CONTEXT_ENV as _STARTUP_ENV, StartupAdmissionClient, StartupAdmissionServer

class LaunchOwner(Protocol):
    """Caller-owned startup custody; the core does not allocate or journal it."""

    session_id: str

    def child_context(self, child_argv: list[str], cwd: str | None) -> str: ...
    def begin_host(self) -> None: ...
    def retain_process(self, proc: Any, state_file: str, nonce: str) -> None: ...
    def admit_host_child(self, context: str, request: dict[str, Any]) -> None: ...

_ACP_STDIO_LIMIT_BYTES = 64 * 1024 * 1024

# Env var carrying the connect-auth nonce to the host process (kept off the
# command line so it does not leak to ps/Task Manager). Stripped before the
# copilot child is spawned so the child never inherits it.
_NONCE_ENV = "AGENT_BRIDGE_SESSION_HOST_NONCE"
_ACQUISITION_ENV = "AGENT_BRIDGE_ORIGINAL_ACQUISITION"

# How much of the host process's combined stdout/stderr (up through
# readiness) to retain for surfacing in an "exited early" RuntimeError. A
# crashed host's own traceback almost always fits in a few KB.
_CRASH_TAIL_CAP_BYTES = 4096


class _CrashTailCapture:
    """Drains a subprocess's combined stdout/stderr into a bounded buffer.

    Continuously reads from ``stream`` on a background thread so the child
    is never blocked on a full OS pipe buffer, keeping only the last
    ``cap_bytes`` -- enough to show a traceback or a shell error from a
    process that exits before reporting ready. The host redirects its own
    stdout/stderr to ``os.devnull`` once it publishes readiness (see
    ``_detach_stdio_from_frontend``), closing this pipe's write end and
    ending the drain; capture is therefore bounded to the bootstrap window,
    not the host's full lifetime.
    """

    def __init__(self, stream: Any, cap_bytes: int = _CRASH_TAIL_CAP_BYTES) -> None:
        self._buf = bytearray()
        self._cap = cap_bytes
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._drain, args=(stream,), daemon=True,
        )
        self._thread.start()

    def _drain(self, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                with self._lock:
                    self._buf.extend(chunk)
                    overflow = len(self._buf) - self._cap
                    if overflow > 0:
                        del self._buf[:overflow]
        except (OSError, ValueError):
            # Stream closed/invalid underneath us (process reaped, etc.) --
            # whatever was captured so far is still usable.
            pass
        finally:
            # Own closing this fd: a successful launch's HostHandle.proc
            # keeps the Popen object (and so this stream) alive for the
            # host's full lifetime otherwise, leaking a frontend file
            # descriptor per launch.
            try:
                stream.close()
            except OSError:
                pass

    def tail_text(self) -> str:
        """The captured bytes decoded as text, most recent last."""
        with self._lock:
            data = bytes(self._buf)
        return data.decode("utf-8", errors="replace")

    def wait_until_drained(self, timeout: float = 2.0) -> None:
        """Block until the drain thread has consumed the stream to EOF.

        A dead process's exit (``proc.poll()``) can be observed before the
        drain thread has read the bytes still buffered in the OS pipe, so
        a ``tail_text()`` taken immediately after can omit (or miss
        entirely) the process's final output. Call this first -- bounded,
        since a process that never actually closes its stdout (unexpected,
        but not this function's job to diagnose) must not hang the caller
        forever.
        """
        self._thread.join(timeout)


def host_spawn_kwargs() -> dict[str, Any]:
    """``subprocess`` kwargs for the FRONTEND to spawn the host so it survives.

    On Windows the host must break away from the daemon's kill-on-close job
    (permitted because that job now carries ``JOB_OBJECT_LIMIT_BREAKAWAY_OK``);
    on POSIX it gets its own session.
    """
    return windowless_daemon_kwargs(breakaway=True)


def _kill_host_process_tree(
    proc: subprocess.Popen, expected_identity: str | None,
) -> None:
    """Kill a spawned-but-never-ready host, reaching its whole tree on POSIX.

    :func:`host_spawn_kwargs` gives the host ``start_new_session=True`` (its
    own process group, ``proc.pid`` doubling as the group id) -- except
    under :func:`contained_test_mode`, where that is suppressed so the
    repository test supervisor retains ownership of the process group, and
    ``os.killpg`` here would reach *its* group instead. ``PR_SET_PDEATHSIG``
    (tying an already-spawned copilot child to its host, see
    ``osutil.child_preexec``) is Linux-only -- on macOS/other POSIX there is
    no equivalent, so killing only the host's own pid can orphan a child it
    already spawned before the timeout; ``os.killpg`` reaches the whole
    group in one call. Windows has no equivalent process-group concept
    here; ``proc.kill()`` (``TerminateProcess``) is used directly.

    This repository's PID-destruction rule (see
    ``local_cache_refresh._kill_tree``, which this mirrors) requires
    identity verification before any destructive termination by numeric id,
    since an exited pid is eventually reusable by an unrelated process.
    ``expected_identity`` is ``process_start_time(proc.pid)`` captured
    immediately after spawn; a ``None`` baseline -- either no identity
    backend at all for this platform (macOS has none) or a miss at spawn
    time -- fails this check closed, same as a confirmed, live, differing
    identity at that pid: neither signals the group, only
    ``proc.kill()`` (the single, still-held ``Popen`` handle, never
    re-resolved by numeric id) runs.
    """
    if sys.platform != "win32" and not contained_test_mode():
        current_identity = process_start_time(proc.pid)
        identity_established = expected_identity is not None
        identity_mismatch = (
            identity_established
            and current_identity is not None
            and current_identity != expected_identity
        )
        if identity_established and not identity_mismatch:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
                return
            except (ProcessLookupError, PermissionError, OSError):
                pass
    proc.kill()



@dataclass
class HostHandle:
    """A launched Session Host process + how to reach it."""

    host_pid: int
    child_pid: int
    port: int
    state_file: str
    proc: subprocess.Popen
    protocol_version: int = proto.PROTOCOL_VERSION
    nonce: str = field(default="", repr=False)


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
    acquisition_launch: LaunchOwner | None = None,
    host_entrypoint: str = "agent_remote_driver.session_host",
    policy_context: str | None = None,
    lifetime_policy: str = "acp",
) -> HostHandle:
    """Spawn a **survivable** Session Host process that owns ``child_argv``.

    The host is launched with :func:`host_spawn_kwargs` so it outlives this
    frontend (Windows job-breakaway / POSIX new-session). It serves a loopback
    reattach endpoint and writes a ``pid``/``child_pid``/``port`` state file,
    which this call waits for. The child inherits ``env`` (so worktree/plan env
    vars reach copilot). Raises ``TimeoutError`` if the host never reports ready.
    ``state_dir`` is lexically anchored to the frontend's cwd before the host
    changes cwd; filesystem links are not dereferenced.

    ``nonce`` is the connect-auth token (generated for ACP when omitted,
    returned on the handle): it is handed to the host
    process **via its environment** (not the command line, so it does not leak
    to ``ps``/Task Manager) and the host requires a matching nonce on ATTACH.
    The copilot child never sees it -- ``run_host`` strips it before spawn.

    An explicit original local owner disables autonomous timeout retirement.
    Its custody cannot expire into an ordinary resumable replacement; STATUS/
    DETACH retain their custody predicate and explicit retirement is separate.

    The host process's own combined stdout/stderr is drained into a small,
    bounded in-memory tail (:class:`_CrashTailCapture`) during bootstrap: if
    it exits early (before reporting ready), the raised ``RuntimeError``
    includes that tail so the real crash reason (a traceback, a missing
    dependency, a bad argv, ...) is diagnosable after the fact instead of
    only ever surfacing a generic exit code (#5384). The host redirects its
    own stdout/stderr to ``os.devnull`` once ready
    (``_detach_stdio_from_frontend``), so output after that point is never
    coupled to this frontend process's lifetime.

    Without ``state_dir`` the host's records live in a labeled scratch
    directory (``scratch-owner.json`` breadcrumb). A launch that fails before
    spawning, or whose host is confirmed to have exited, removes that
    directory; any other outcome keeps it because a live host or a later
    authority read may still need it.
    """
    # Frontend-only helper; the compact host archive does not carry it.
    from .launcher_scratch import launch_state

    with launch_state(state_dir, session_id, acquisition_launch) as (sd, phase):
        return _launch_in(
            sd, child_argv, cwd=cwd, env=env, ready_timeout=ready_timeout, nonce=nonce,
            session_id=session_id, host_version=host_version,
            unexpected_reap_seconds=unexpected_reap_seconds,
            active_reap_seconds=active_reap_seconds, acquisition_launch=acquisition_launch,
            host_entrypoint=host_entrypoint, policy_context=policy_context,
            lifetime_policy=lifetime_policy, phase=phase,
        )


def _launch_in(
    sd: Path,
    child_argv: list[str],
    *,
    cwd: str | None,
    env: dict[str, str] | None,
    ready_timeout: float,
    nonce: str,
    session_id: str,
    host_version: str,
    unexpected_reap_seconds: float,
    active_reap_seconds: float,
    acquisition_launch: LaunchOwner | None,
    host_entrypoint: str,
    policy_context: str | None,
    lifetime_policy: str,
    phase: dict[str, bool],
) -> HostHandle:
    from agent_procutil._daemon_instance_store import _root
    from .launcher_scratch import observe_exit, tracked_creation

    _root(sd, create=True)
    state_file = sd / f"host-{os.getpid()}-{int(time.time()*1000)}.json"

    if host_entrypoint == "agent_remote_driver.session_host":
        from agent_procutil._interpreter import physical_executable
        from .bundle import build_session_host_bundle

        interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
        if interpreter is None:
            raise RuntimeError("Independent Session Host requires a physical base interpreter")
        archive, _digest = build_session_host_bundle(sd / "runtime")
        executable = [interpreter, "-I", "-S", str(archive)]
        support = subprocess.run(
            [*executable, "--support-json"],
            capture_output=True, text=True, timeout=15, creationflags=no_window_flags(),
        )
        try:
            declaration = json.loads(support.stdout)
            if (
                support.returncode != 0
                or declaration.get("contract") != "agent-remote-driver.session-host"
                or type(declaration.get("version")) is not int or declaration["version"] != 1
                or type(declaration.get("protocol_version")) is not int
                or declaration["protocol_version"] != proto.PROTOCOL_VERSION
                or any(
                    type(declaration.get(key)) is not list
                    or not all(type(item) is str for item in declaration[key])
                    for key in ("modes", "transports", "lifetime_policies")
                )
                or "headless" not in declaration["modes"]
                or "tcp-frame" not in declaration["transports"]
                or lifetime_policy not in declaration["lifetime_policies"]
                or acquisition_launch is not None
                and (
                    type(declaration.get("startup_admission")) is not list
                    or not all(type(item) is str for item in declaration["startup_admission"])
                    or "live-owner-v1" not in declaration["startup_admission"]
                )
            ):
                raise ValueError("unsupported selected Host")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise RuntimeError("Selected Session Host does not support this execution request") from exc
    else:
        executable = [sys.executable, "-m", host_entrypoint]
    host_argv = [*executable,
                 "--port", "0", "--state-file", str(state_file),
                 "--local-birth-evidence"]
    if session_id:
        host_argv += ["--session-id", session_id]
    if host_version:
        host_argv += ["--host-version", host_version]
    if cwd:
        host_argv += ["--cwd", cwd]
    if policy_context is not None:
        host_argv += ["--repository-policy-context", policy_context]
    host_argv += ["--lifetime-policy", lifetime_policy]
    retained_host = acquisition_launch is not None
    host_argv += ["--unexpected-reap-seconds", str(0.0 if retained_host else unexpected_reap_seconds)]
    host_argv += ["--active-reap-seconds", str(0.0 if retained_host else active_reap_seconds)]
    host_argv += ["--", *child_argv]

    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    nonce = nonce or child_env.get(_NONCE_ENV, "")
    if not nonce and lifetime_policy == "acp":
        nonce = secrets.token_hex(32)
    if nonce:
        child_env[_NONCE_ENV] = nonce
    child_env.pop(_ACQUISITION_ENV, None)
    child_env.pop(_STARTUP_ENV, None)
    startup = None
    if acquisition_launch is not None:
        if session_id != acquisition_launch.session_id:
            raise ValueError("Local Host launch requires its original local owner")
        context = acquisition_launch.child_context(child_argv, cwd)
        startup = StartupAdmissionServer(
            lambda request: acquisition_launch.admit_host_child(context, request),
            environment_keys=("AGENT_BRIDGE_SESSION_ID",),
            timeout=90.0,
        )
        child_env[_STARTUP_ENV] = startup.context

    spawn_options = host_spawn_kwargs()

    try:
        if acquisition_launch is not None:
            acquisition_launch.begin_host()
        proc = spawn_instance(
            host_argv,
            popen=tracked_creation(phase),
            instance=DaemonInstanceSpec(
                "agent-bridge-session-host", sd, Path(__file__).resolve(),
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=child_env,
            cwd=cwd or None,
            **spawn_options,
        )
        if acquisition_launch is not None:
            acquisition_launch.retain_process(proc, str(state_file), nonce)
        if startup is not None:
            startup.start(proc)
    except BaseException:
        if startup is not None:
            startup.close()
        raise
    crash_tail = _CrashTailCapture(proc.stdout)
    expected_identity: str | None = None
    if sys.platform != "win32" and not contained_test_mode():
        # Captured immediately after spawn, before any reaping could occur
        # -- the baseline _kill_host_process_tree verifies against if a
        # ready-timeout later needs to kill this process's group.
        expected_identity = process_start_time(proc.pid)

    deadline = time.time() + ready_timeout
    while True:
        if proc.poll() is not None:
            observe_exit(phase, proc)
            if startup is not None:
                startup.close()
            # The process has exited, but the OS pipe may still hold bytes
            # the drain thread hasn't consumed yet -- wait for it to reach
            # EOF before reading the tail, so the final traceback line
            # isn't dropped by a race between poll() and the reader.
            crash_tail.wait_until_drained()
            tail_text = crash_tail.tail_text().strip()
            detail = f"\n--- last output ---\n{tail_text}" if tail_text else ""
            raise RuntimeError(
                f"session host exited early (code={proc.returncode}) "
                f"before ready{detail}"
            ) from (startup.error if startup is not None else None)
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}
            if data.get("port") and data.get("child_pid"):
                if startup is not None:
                    startup.close()
                return HostHandle(
                    host_pid=int(data["pid"]),
                    child_pid=int(data["child_pid"]),
                    port=int(data["port"]),
                    state_file=str(state_file),
                    proc=proc,
                    protocol_version=int(data.get("protocol_version",
                                                  proto.PROTOCOL_VERSION)),
                    nonce=data.get("nonce", nonce),
                )
        if time.time() >= deadline:
            # Recheck exit/readiness one more time right at the boundary
            # (above) before giving up -- the final sleep below can cross
            # the deadline after the host already published a complete
            # ready-state file or exited, and without this re-check that
            # publication would be ignored and a now-ready host killed.
            break
        time.sleep(0.05)

    # The host neither became ready nor exited within the deadline -- stop
    # waiting on it rather than leaking a hung process (and anything it
    # already spawned), an unbounded-lifetime daemon thread, and its pipe
    # file descriptor.
    if acquisition_launch is not None:
        raise TimeoutError(
            f"original session host readiness is unknown after {ready_timeout}s; retaining custody"
        )
    _kill_host_process_tree(proc, expected_identity)
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        pass
    crash_tail.wait_until_drained()
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


def _detach_stdio_from_frontend() -> None:
    """Redirect this (host) process's own stdout/stderr to ``os.devnull``.

    The frontend's :func:`launch_session_host` pipes the host's stdout/stderr
    only to capture a crash-tail for its own ready-wait loop
    (:class:`_CrashTailCapture`); that pipe's sole reader is a daemon thread
    in the frontend process. The host is explicitly required to outlive the
    frontend (module docstring), so once it has published readiness it must
    stop depending on that pipe -- otherwise a later frontend exit/restart
    closes the read end and any subsequent host stdout/stderr write raises
    ``BrokenPipeError``. ``os.dup2`` over the inherited fds closes the pipe's
    write end too, which cleanly ends the frontend's drain thread (read()
    returns EOF) instead of leaving it blocked. Idempotent and best-effort.
    """
    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(devnull_fd, sys.stdout.fileno())
        os.dup2(devnull_fd, sys.stderr.fileno())
    except (OSError, ValueError):
        pass
    finally:
        os.close(devnull_fd)


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
    *, pass_fds: tuple[int, ...] = (),
    policy_context: str | None = None,
    before_exec: Callable[[], None] | None = None,
) -> asyncio.subprocess.Process:
    _require_receiving_policy(cwd, policy_context)
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    # Host-only payload and attach context must be re-established by the
    # Copilot child's own plugin hooks, never inherited from this launcher.
    child_env.pop(_NONCE_ENV, None)
    child_env.pop(_STARTUP_ENV, None)
    child_env.pop("COPILOT_PLUGIN_ROOT", None)
    if not pass_fds:
        from ..preference_attestation import DIGEST_ENV, FD_ENV, MODE_ENV, NONCE_ENV, TARGET_ENV

        for key in (DIGEST_ENV, FD_ENV, MODE_ENV, NONCE_ENV, TARGET_ENV):
            child_env.pop(key, None)
    # POSIX/Linux: arm PR_SET_PDEATHSIG so copilot dies with the host even on a
    # hard host kill -- the Linux counterpart to the Windows kill-on-close job,
    # so a remote (mesh/CodeSpace) far side never orphans copilot. None (default)
    # on Windows, where preexec_fn is unsupported.
    preexec = child_preexec()
    spawn_argv = _resolve_child_exe(argv, child_env.get("PATH"))
    admission = child_env.pop(_ACQUISITION_ENV, None)
    if admission is not None:
        if before_exec is None:
            raise ValueError("Original launch context requires its receiving owner")
    if before_exec is not None:
        before_exec()
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
        **({"pass_fds": pass_fds} if pass_fds else {}),
    )


@daemon_entrypoint("agent-bridge-session-host", source=__file__, purpose="production")
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
    local_birth_evidence: bool = False,
    policy_context: str | None = None,
    before_exec: Callable[[], None] | None = None,
    lifetime_policy: str = "acp",
    ws_port: int | None = None,
    backend: str = "native-acp",
) -> None:
    """Spawn the child, serve the reattachable endpoint, run until closed.

    ``nonce`` or ``AGENT_BRIDGE_SESSION_HOST_NONCE`` selects connect-auth.
    ACP generates a private token when neither is supplied; only explicit
    legacy execution permits an unauthenticated listener.
    ``unexpected_reap_seconds`` bounds how long an idle, front-less child lingers
    after an *unexpected* disconnect before the host self-reaps it (0 disables).
    ``active_reap_seconds`` bounds how long a still-active (mid-turn) front-less
    child is held after an unexpected disconnect before the host lets it go (0
    disables -- an active child then lives until its own stop).
    """
    if lifetime_policy not in {"acp", "legacy"}:
        raise ValueError("Unsupported Session Host lifetime policy")
    if backend not in {"native-acp", "sdk"} or backend == "sdk" and lifetime_policy != "acp":
        raise ValueError("Unsupported Session Host backend or lifetime policy")
    _require_receiving_policy(cwd, policy_context)
    sdk_token = ""
    if backend == "sdk":
        from .sdk_backend import connect_owner, require_sdk, validate_child_argv

        require_sdk()
        validate_child_argv(child_argv)
        sdk_token = secrets.token_hex(32)
        env = {**(env or {}), "COPILOT_CONNECTION_TOKEN": sdk_token}
    if ws_port is not None and (lifetime_policy != "acp" or not 0 <= ws_port <= 65535):
        raise ValueError("WebSocket requires ACP lifetime and a valid loopback port")
    # Resolve optional receiving support before creating a child. The compact
    # TCP archive deliberately doesn't vendor third-party distributions.
    if ws_port is not None:
        from .ws_transport import WebSocketTransport
    apply_host_survival()
    nonce = nonce or (env or {}).get(_NONCE_ENV, "") or os.environ.get(_NONCE_ENV, "")
    if not nonce and lifetime_policy == "acp":
        nonce = secrets.token_hex(32)
    admission = (env or {}).get(_ACQUISITION_ENV) or os.environ.get(_ACQUISITION_ENV)
    startup_context = (env or {}).get(_STARTUP_ENV) or os.environ.get(_STARTUP_ENV)
    startup = StartupAdmissionClient(startup_context) if startup_context is not None else None
    effective_env = {**os.environ, **(env or {})}
    receiving_check = before_exec
    def admit_execution() -> None:
        _require_receiving_policy(cwd, policy_context)
        if startup is not None:
            startup.check(child_argv, cwd, session_id, effective_env)
        if admission is not None:
            if receiving_check is None:
                raise ValueError("Original launch context requires its receiving owner")
        if receiving_check is not None:
            receiving_check()

    try:
        if admission is not None or startup is not None:
            admit_execution()
        from ..preference_attestation import SHELL_MARKER

        from functools import partial

        spawn = partial(
            _spawn_child, policy_context=policy_context, before_exec=admit_execution,
        )
        if child_argv and child_argv[0] == SHELL_MARKER:
            from .preference_spawn import spawn_attested

            child, preference_receipt = await spawn_attested(
                child_argv, cwd, env, spawn,
                before_exec=admit_execution,
            )
        else:
            child = await spawn(child_argv, cwd, env)
            preference_receipt = execution_receipt(child_argv, env, child.pid or 0)
    finally:
        if startup is not None:
            startup.close()
    native_child = child
    sdk_child = None
    if backend == "sdk":
        try:
            sdk_child = await connect_owner(
                native_child, token=sdk_token, cwd=cwd or os.getcwd(), session_id=session_id,
            )
            child = sdk_child
        except BaseException:
            if native_child.returncode is None:
                native_child.kill()
            await native_child.wait()
            raise
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
                       on_child_exit=_publish_child_exit,
                       preference_receipt=preference_receipt,
                       lifetime_policy=lifetime_policy)
    websocket = None
    tasks: list[asyncio.Task] = []
    try:
        bound_port = await host.serve(port=port)
        if ws_port is not None:
            websocket = WebSocketTransport(bound_port, nonce)
            await websocket.serve(ws_port)
        # The shared native observer is part of the compact receiving closure.
        birth_evidence = {}
        if local_birth_evidence:
            birth_evidence = {
                "host_process_start_time": process_start_time(os.getpid()),
                "child_process_start_time": process_start_time(child.pid),
            }

        state.update({
            "version": 2,
            "session_id": session_id,
            "pid": os.getpid(),
            "host_pid": os.getpid(),
            "child_pid": child.pid,
            "port": bound_port,
            "protocol_version": proto.PROTOCOL_VERSION,
            "lifetime_policy": lifetime_policy,
            "backend": backend,
            "automatic_idle_retirement": (
                "legacy-disconnect" if lifetime_policy == "legacy" else "unavailable"
            ),
            "host_version": host_version,
            "nonce": nonce,
            "created_at": time.time(),
            "state": "running",
            "child_executable": (
                "bash" if child_argv and child_argv[0] == SHELL_MARKER
                else child_argv[0] if child_argv else ""
            ),
            "cwd": cwd or "",
            "reverse_forwards": list(reverse_forwards or []),
            "boot_id": _boot_id(),
            "host_start_ticks": _process_start_ticks(os.getpid()),
            "child_start_ticks": _process_start_ticks(child.pid),
            **birth_evidence,
        })
        if websocket is not None:
            state["websocket"] = {
                "contract": "acp.v1", "url": f"ws://127.0.0.1:{websocket.port}/acp",
                "nonce": websocket.nonce,
            }
        if state_path is not None:
            _write_host_state(state_path, state)
            if not host.child_alive:
                exit_code = host.child_exit_code
                if exit_code is None:
                    exit_code = child.returncode
                if exit_code is not None:
                    _publish_child_exit(exit_code)
        if not contained_test_mode():
            # Readiness published: no longer depend on the caller's crash-tail pipe.
            _detach_stdio_from_frontend()
        if ready is not None:
            ready.set()
        server_task = asyncio.create_task(host.serve_forever())
        tasks.append(server_task)
        child_task = asyncio.create_task(host.wait_child()) if lifetime_policy != "legacy" else None
        if child_task is not None:
            tasks.append(child_task)
        if child_task is None:
            await server_task
        else:
            done, _pending = await asyncio.wait(
                (server_task, child_task), return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                await task
    finally:
        for task in tasks:
            task.cancel()
        try:
            if websocket is not None:
                await websocket.close(drain=True)
        finally:
            try:
                await host.close()
            finally:
                try:
                    # The server task may await clients that only host.close() releases.
                    await asyncio.gather(*tasks, return_exceptions=True)
                finally:
                    # Reap within the loop, even if a transport failed to close.
                    try:
                        if sdk_child is not None:
                            await sdk_child.close()
                    finally:
                        if native_child.returncode is None:
                            try:
                                native_child.kill()
                            except ProcessLookupError:
                                pass
                            try:
                                await native_child.wait()
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


def _require_receiving_policy(cwd: str | None, context: str | None) -> None:
    if context is None:
        from repository_policy import require_agent_hosting

        require_agent_hosting(cwd or os.getcwd())
    else:
        from .receiver_policy import require_portable_path_hosting

        require_portable_path_hosting(cwd or os.getcwd(), context)


def main(
    argv: list[str] | None = None, *, run: Callable[..., Any] = run_host,
    legacy: bool = False,
) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from importlib.util import find_spec

    headed_available = not legacy and find_spec("agent_remote_driver.headed") is not None
    lifetime_policies = ("legacy",) if legacy else (
        ("acp", "legacy", "cli") if headed_available else ("acp", "legacy")
    )
    if argv == ["--support-json"]:
        sdk_available = False
        if not legacy and find_spec("agent_remote_driver.session_host.sdk_backend") is not None:
            from .sdk_backend import available

            sdk_available = available()
        websocket_available = (
            not legacy
            and find_spec("agent_remote_driver.session_host.ws_transport") is not None
            and find_spec("websockets") is not None
        )
        print(json.dumps({
            "contract": "agent-remote-driver.session-host",
            "version": 1,
            "modes": ["headless"] + (["headed"] if headed_available else []),
            "headless_backends": ["native-acp"] + (["sdk"] if sdk_available else []),
            "transports": ["tcp-frame"] + (["acp.v1"] if websocket_available else []),
            "protocol_version": proto.PROTOCOL_VERSION,
            "lifetime_policies": list(lifetime_policies),
            "startup_admission": ["live-owner-v1"],
            "headed_process_receipts": ["native-child-v1"] if headed_available else [],
            "headed_client": ["native-send-v1"] if headed_available else [],
            "automatic_idle_retirement": {
                "availability": "unavailable",
                "reason": "structured-runtime-work-inventory-unqualified",
            },
        }))
        return 0
    if argv[:2] == ["--preference-exec", "--"]:
        from ..preference_exec import main as preference_exec_main

        # zipapp's generated entry point ignores return values.
        raise SystemExit(preference_exec_main(argv[2:]))
    ap = argparse.ArgumentParser(
        prog="python -m agent_remote_driver.session_host",
        description="Standalone Session Host: own a Copilot --acp child, serve reattach.",
    )
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--ws-port", type=int, default=None)
    ap.add_argument("--state-file", default=None)
    ap.add_argument("--session-id", default="")
    ap.add_argument("--host-version", default="")
    ap.add_argument("--reverse-forward", action="append", default=[])
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--unexpected-reap-seconds", type=float, default=60.0)
    ap.add_argument("--active-reap-seconds", type=float, default=0.0)
    ap.add_argument("--local-birth-evidence", action="store_true")
    ap.add_argument("--repository-policy-context", default=None)
    ap.add_argument("--mode", choices=("headless", "headed") if headed_available else ("headless",),
                    default="headless")
    ap.add_argument("--backend", choices=("native-acp",) if legacy else ("native-acp", "sdk"),
                    default="native-acp")
    ap.add_argument("--attempt-directory", default=None)
    ap.add_argument(
        "--lifetime-policy", choices=lifetime_policies,
        default=None,
    )
    ap.add_argument("child", nargs=argparse.REMAINDER,
                    help="child command after `--` (e.g. -- copilot --acp --stdio)")
    args = ap.parse_args(argv)
    if legacy and args.ws_port is not None:
        ap.error("WebSocket is unsupported by the legacy entrypoint")

    child_argv = args.child
    if child_argv and child_argv[0] == "--":
        child_argv = child_argv[1:]
    if not child_argv:
        ap.error("a child command is required after `--`")
    lifetime_policy = args.lifetime_policy or (
        "cli" if args.mode == "headed" else "legacy" if legacy else "acp"
    )
    if args.mode == "headed":
        if args.backend != "native-acp":
            ap.error("SDK backend selection is supported only for headless execution")
        if (
            lifetime_policy != "cli" or not args.attempt_directory
            or not args.state_file or not args.session_id
        ):
            ap.error("headed execution requires cli lifetime, attempt directory, state file and session id")
        if (
            args.host_version or args.reverse_forward or args.local_birth_evidence
            or args.active_reap_seconds != 0.0 or args.unexpected_reap_seconds != 60.0
        ):
            ap.error("headless-only options are unsupported for headed execution")
        from ..headed.launcher import run_headed

        # The same terminal event reaches native Copilot. Let it cancel or exit;
        # a non-ignored Python handler is reset on exec rather than inherited as SIG_IGN.
        interrupt_signals = [signal.SIGINT]
        if hasattr(signal, "SIGBREAK"):
            interrupt_signals.append(signal.SIGBREAK)
        previous_handlers = {}
        try:
            for signum in interrupt_signals:
                previous_handlers[signum] = signal.signal(signum, lambda _signal, _frame: None)
            result = asyncio.run(run_headed(
                child_argv, session_id=args.session_id, attempt_directory=args.attempt_directory,
                state_file=args.state_file, cwd=args.cwd, port=args.port, ws_port=args.ws_port,
                policy_context=args.repository_policy_context,
            ))
            return 128 - result if os.name == "posix" and result < 0 else result
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
    if lifetime_policy == "cli" or args.attempt_directory is not None:
        ap.error("cli lifetime and attempt directory require headed execution")

    try:
        asyncio.run(run(
            child_argv,
            port=args.port,
            state_file=args.state_file,
            cwd=args.cwd,
            session_id=args.session_id,
            host_version=args.host_version,
            reverse_forwards=args.reverse_forward,
            unexpected_reap_seconds=args.unexpected_reap_seconds,
            active_reap_seconds=args.active_reap_seconds,
            local_birth_evidence=args.local_birth_evidence,
            policy_context=args.repository_policy_context,
            lifetime_policy=lifetime_policy,
            ws_port=args.ws_port,
            **({"backend": args.backend} if args.backend != "native-acp" else {}),
        ))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
