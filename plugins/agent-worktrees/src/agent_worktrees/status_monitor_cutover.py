"""Installer-driven status-monitor cutover helpers."""

from __future__ import annotations

import json
import os
import secrets
import select
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlparse

from agent_procutil import spawn_daemon, windowless_python, windowless_python_env
from single_instance_lease import AlreadyRunningError, SingleInstance
from zdd import breadcrumb, routing
from zdd.cutover import CutoverOrchestrator

from . import locks
from .status_monitor_drain import DrainTrace
from .status_monitor_python import direct_monitor_python
from .handoff_prompt_receipt_control import ACTION as RECEIPT_ACTION, FRAME_BYTES, unique_object

_BIND = "127.0.0.1"
_READ_TIMEOUT_S = 5.0
_CUTOVER_LOCK_TIMEOUT_S = 300.0

# A single resident sweep pass (core._monitor_sweep) is one atomic,
# non-interruptible unit of work covering every tracked worktree across every
# registered project/machine -- "busy: ['sweep']" in a drain's busy_sessions
# means that pass hasn't finished yet, not that anything is stuck. On a large
# fleet that one pass can legitimately take well over 30s (observed
# 44s-2m10s+), and this module's own `activate_after_update()` was overriding
# CutoverOrchestrator's already-generous built-in drain_timeout default
# (zdd.cutover.CutoverOrchestrator, 300.0s) down to a needlessly aggressive
# 30.0s -- making cutover's drain lose the race essentially every time and
# leave the superseded daemon un-reaped, accumulating orphaned
# status-monitor processes on every auto-update (#5326). Fix: stop
# overriding the orchestrator's own default drain_timeout, and align
# health_timeout with its default too (both were previously hardcoded here
# to match the orchestrator's defaults anyway, so this is a no-op for
# health_timeout and a pure increase for drain_timeout). Both remain
# overridable per-machine via env var so an even larger fleet can be tuned
# without another code change.
def _env_timeout(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw:
        try:
            parsed = float(raw)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return default


_DEFAULT_HEALTH_TIMEOUT_S = _env_timeout("AGENT_WORKTREES_STATUS_MONITOR_HEALTH_TIMEOUT", 60.0)
_DEFAULT_DRAIN_TIMEOUT_S = _env_timeout("AGENT_WORKTREES_STATUS_MONITOR_DRAIN_TIMEOUT", 300.0)


def routing_dir(runtime_home: Path | None = None) -> Path:
    from . import status_monitor_runtime as smr

    root = runtime_home if runtime_home is not None else smr._aw_runtime_home()
    return Path(root) / "status-monitor-routing"


def pick_free_port(bind: str = _BIND) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((bind, 0))
        return int(sock.getsockname()[1])


def control_token_path(runtime_home: Path | None = None) -> Path:
    return (runtime_home if runtime_home is not None else routing_dir().parent) / "status-monitor-control.token"


def load_or_create_control_token(runtime_home: Path | None = None) -> str:
    path = control_token_path(runtime_home)
    try:
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token


class _ControlServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address, handler, *, owner, token: str):
        self.owner = owner
        self.token = token
        super().__init__(address, handler)

    def process_request(self, request, client_address) -> None:
        self.owner._on_request_accepted()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.owner._on_request_finished()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.owner._on_request_finished()


class _ControlHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        current_read = False
        try:
            self.request.settimeout(_READ_TIMEOUT_S)
            raw = self.rfile.readline(256 * 1024)
            request = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(request, dict)
                or not secrets.compare_digest(
                    str(request.get("token") or ""), self.server.token  # type: ignore[attr-defined]
                )
            ):
                return
            action = str(request.get("action") or "health")
            if action == RECEIPT_ACTION and (
                len(raw) > FRAME_BYTES or not raw.endswith(b"\n")
                or set(request) != {"version", "token", "action", "payload"}
                or type(request.get("version")) is not int or request["version"] != 1
            ):
                self.wfile.write(b'{"version":1,"result":{"contractVersion":1,'
                                 b'"status":"unavailable","reason":"invalid-frame",'
                                 b'"replayed":false,"subject":null,"owner":null}}\n')
                return
            if action == RECEIPT_ACTION:
                request = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
            payload = request.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            owner = self.server.owner
            if action == "worktrees-read":
                owner._on_current_read_started()
                current_read = True
            if owner.stream_handle is not None and action == "worktrees-feed":
                def disconnected():
                    readable, _, _ = select.select([self.request], [], [], 0)
                    return bool(readable)

                frames = owner.stream_handle(action, payload, disconnected)
                try:
                    for frame in frames:
                        self.wfile.write(
                            json.dumps(frame, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                            + b"\n"
                        )
                        self.wfile.flush()
                finally:
                    frames.close()
                return
            result = self.server.owner.handle(action, payload)  # type: ignore[attr-defined]
            if not isinstance(result, dict):
                result = {}
            encoded = json.dumps(
                {"version": 1, "result": result}, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8") + b"\n"
            if action == RECEIPT_ACTION and len(encoded) > FRAME_BYTES:
                encoded = (b'{"version":1,"result":{"contractVersion":1,'
                           b'"status":"unknown","reason":"response-too-large",'
                           b'"replayed":false,"subject":null,"owner":null}}\n')
            self.wfile.write(encoded)
        except Exception:
            return
        finally:
            if current_read:
                self.server.owner._on_current_read_finished()


class ControlServer:
    """Loopback control plane for the resident status-monitor."""

    def __init__(
        self,
        handle: Callable[[str, dict], dict],
        *,
        port: int | None = None,
        token: str | None = None,
        stream_handle: Callable | None = None,
    ):
        self._handle = handle
        self.stream_handle = stream_handle
        self._active_handlers = 0
        self._current_read_handlers = 0
        self._active_handlers_lock = threading.Lock()
        self.token = token or load_or_create_control_token()
        bind = (_BIND, int(port) if port is not None else 0)
        self.server = _ControlServer(bind, _ControlHandler, owner=self, token=self.token)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="agent-worktrees-status-monitor-control",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    def handle(self, action: str, payload: dict) -> dict:
        return self._handle(action, payload)

    def _on_request_accepted(self) -> None:
        with self._active_handlers_lock:
            self._active_handlers += 1

    def _on_request_finished(self) -> None:
        with self._active_handlers_lock:
            self._active_handlers -= 1

    def active_handler_count(self) -> int:
        with self._active_handlers_lock:
            return self._active_handlers

    def _on_current_read_started(self) -> None:
        with self._active_handlers_lock:
            self._current_read_handlers += 1

    def _on_current_read_finished(self) -> None:
        with self._active_handlers_lock:
            self._current_read_handlers -= 1

    def current_read_count(self) -> int:
        with self._active_handlers_lock:
            return self._current_read_handlers


class ControlClient:
    """Small JSON-line client for the status-monitor control surface."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        config_dir: Path | None = None,
        timeout: float = 5.0,
        transition_custody: ExitStack | None = None,
    ):
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "") or not parsed.hostname or parsed.port is None:
            raise ValueError(f"invalid status-monitor control URL: {base_url!r}")
        self.host = parsed.hostname
        self.port = int(parsed.port)
        self.base_url = base_url
        self.token = token or load_or_create_control_token()
        self.config_dir = Path(config_dir) if config_dir is not None else None
        self.timeout = timeout
        self.transition_custody = transition_custody

    def _request(
        self, action: str, payload: dict | None = None, *,
        max_response_bytes: int | None = None, total_timeout: float | None = None,
        read_timeout: float | None = None,
    ) -> dict:
        body = json.dumps(
            {"version": 1, "token": self.token, "action": action, "payload": payload or {}},
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        deadline = None if total_timeout is None else time.monotonic() + total_timeout
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(self.timeout if read_timeout is None else read_timeout)
            sock.sendall(body)
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            size = 0
            while True:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("status-monitor current-roster read deadline exceeded")
                    sock.settimeout(min(self.timeout, remaining))
                chunk = sock.recv(65536)
                if not chunk:
                    break
                size += len(chunk)
                if max_response_bytes is not None and size > max_response_bytes:
                    raise ValueError("status-monitor response exceeds the point-read bound")
                chunks.append(chunk)
                if chunk.endswith(b"\n"):
                    break
        buf = b"".join(chunks)
        response = json.loads(buf.decode("utf-8"))
        result = response.get("result")
        if not isinstance(result, dict):
            raise ValueError("status-monitor control response missing result")
        return result

    def health(self) -> dict:
        return self._request("health")

    def worktrees_read(self, payload: dict) -> dict:
        from .worktrees_runtime import READ_ACTION
        from .worktrees_projection import MAX_ROW_BYTES

        point = bool(payload.get("worktree_id") or payload.get("codename"))
        return self._request(
            READ_ACTION, payload,
            max_response_bytes=MAX_ROW_BYTES + 16384 if point else None,
            total_timeout=self.timeout,
        )

    def drain(self, *, timeout: float, poll: float, force: bool) -> dict:
        with ExitStack() as local_custody:
            return self._drain(timeout, poll, force, self.transition_custody or local_custody)

    def _drain(self, timeout: float, poll: float, force: bool, custody: ExitStack) -> dict:
        trace = DrainTrace(
            self.config_dir or routing_dir(), role="client", port=self.port,
            timeout=timeout, poll=poll, rpc_timeout=self.timeout, force=force,
        )
        with trace.stage("select-successor"):
            active = (
                routing.read_active_endpoint(self.config_dir, verify_listener=False)
                if self.config_dir is not None else None
            )
        if active is not None and active.base_url != self.base_url:
            with trace.stage("promote-successor"):
                promoted = ControlClient(
                    active.base_url, token=self.token,
                    config_dir=self.config_dir, timeout=self.timeout,
                ).promote()
                if not promoted.get("adopted"):
                    raise ValueError("status-monitor successor refused promotion")
                from .runtime_monitor_demand import retain_transition_demand

                custody.enter_context(retain_transition_demand(self.config_dir.parent, active.pid))
        with trace.stage("request-predecessor"):
            result = self._request(
                "drain", {"timeout": timeout, "poll": poll, "force": force},
                read_timeout=max(self.timeout, timeout + 5.0),
            )
        trace.emit(
            "response", "ok" if result.get("drained") else "fail",
            reasons=result.get("busy_sessions"), receipt=result,
        )
        return result

    def undrain(self) -> dict:
        return self._request("undrain")

    def shutdown(self) -> dict:
        return self._request("shutdown")

    def adopt_relay(self) -> dict:
        return self.promote()

    def promote(self) -> dict:
        return self._request("promote")


MONITOR_LOG_NAME = "status-monitor.log"


def monitor_log_path(cwd: str) -> Path:
    """Where a resident monitor generation appends its stdout/stderr.

    ``<install root>/logs/status-monitor.log``. When the install root is not
    materialized yet the daemon runs from HOME; its log is then a hidden file
    in that (owner-only) directory, never a predictable name in a shared temp
    directory that another user could pre-create.
    """
    home = os.path.normcase(os.path.expanduser("~"))
    if os.path.normcase(os.path.abspath(cwd)) == home:
        return Path(os.path.abspath(cwd)) / f".agent-worktrees-{MONITOR_LOG_NAME}"
    return Path(os.path.abspath(cwd)) / "logs" / MONITOR_LOG_NAME


def spawn_monitor(argv: list[str], env: dict[str, str], *, console_children: bool):
    """Launch a resident status-monitor generation through ``spawn_daemon``.

    Runs from the monitor's install root (``_daemon_cwd``), breaks away from a
    kill-on-close Job around the caller (an installer, an updater, a hook) and
    appends its stdout/stderr to :func:`monitor_log_path`. A Job that forbids
    breakaway fails the spawn rather than starting a monitor that would die
    with its caller.
    """
    from . import status_monitor_runtime as smr

    cwd = smr._daemon_cwd()
    return spawn_daemon(
        argv, cwd=cwd, log_path=monitor_log_path(cwd), env=env,
        console_children=console_children,
        display="agent_worktrees " + " ".join(argv[3:5]),
    )


def spawn_passive(runtime_python: str, *, port: int):
    from . import status_monitor_runtime as smr

    child_env = smr._daemon_environment()
    child_env["AGENT_WORKTREES_STATUS_MONITOR_ROUTING_DIR"] = str(routing_dir())
    argv = [
        runtime_python,
        "-m",
        "agent_worktrees",
        "status-monitor",
        "--passive",
        "--control-port",
        str(port),
    ]
    from .runtime_monitor_launch import retained_monitor_argv

    argv = retained_monitor_argv(argv)
    if os.name == "nt":
        argv[0], extra_env = direct_monitor_python(runtime_python)
    else:
        argv[0] = windowless_python(runtime_python)
        extra_env = windowless_python_env(runtime_python)
    child_env.update(extra_env)
    # Windows: periodic console children inherit one hidden console tree.
    return spawn_monitor(argv, child_env, console_children=os.name == "nt")


def _monitor_control_url_from_route() -> str | None:
    endpoint = routing.read_active_endpoint(routing_dir())
    if endpoint is None:
        return None
    return endpoint.base_url


def _monitor_lock_is_live() -> bool:
    from . import status_monitor_runtime as smr

    return locks.lock_is_live(locks.read_lock(smr._monitor_lock_path()))


def routed_monitor_identity(runtime_home: Path | None = None) -> tuple[int, str, str | None] | None:
    """Authenticate a stable routed owner before installer retention or cutover."""
    from zdd import diagnostics

    directory = routing_dir() if runtime_home is None else routing_dir(runtime_home)
    snapshot = routing.read_table(directory, strict=True)
    endpoints = []
    for key in ("active", "previous"):
        raw = (snapshot or {}).get(key)
        if raw is None:
            continue
        endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        if endpoint is None or type(endpoint.pid) is not int or endpoint.pid <= 0:
            raise ValueError(f"Routed monitor {key} identity is malformed")
        endpoints.append((key, endpoint))
    endpoint = None
    for key, candidate in endpoints:
        if diagnostics._pid_alive(candidate.pid):
            if key != "active":
                raise ValueError("Live fallback monitor requires owner-led route recovery")
            endpoint = candidate
            break
    if endpoint is None:
        return None
    pid = endpoint.pid
    if type(pid) is not int or pid <= 0:
        raise ValueError("Routed monitor has no attributable PID")
    if not diagnostics._pid_alive(pid):
        return None
    before = locks.process_start_time(pid)
    if before is None or (
        endpoint.process_start_time is not None and endpoint.process_start_time != before
    ):
        raise ValueError("Routed monitor process identity is unconfirmed")
    client_options = {} if runtime_home is None else {"token": load_or_create_control_token(runtime_home)}
    health = ControlClient(endpoint.base_url, **client_options).health()
    if (
        health.get("pid") != pid
        or health.get("status") not in {"ready", "draining", "unavailable"}
        or locks.process_start_time(pid) != before
        or routing.read_table(directory, strict=True) != snapshot
    ):
        raise ValueError("Routed monitor identity changed during admission")
    slot = health.get("runtime_slot")
    return pid, before, slot if isinstance(slot, str) else None


def _health_check(host: str, port: int) -> bool:
    try:
        status = ControlClient(f"http://{host}:{port}").health()
        if status.get("status") != "ready":
            return False
        roster = status.get("worktrees")
        if not isinstance(roster, dict):
            return True
        return roster.get("status") == "ready" or (
            status.get("published") is False and roster.get("status") == "unready"
        )
    except Exception:
        return False


def _make_client(base_url: str) -> ControlClient:
    return ControlClient(base_url, config_dir=routing_dir(), timeout=65.0)


def _predecessor_health_check(host: str, port: int) -> bool:
    """A fenced resident can recover only after rollback restores its election."""
    try:
        status = ControlClient(f"http://{host}:{port}").health()
        roster = status.get("worktrees")
        return status.get("status") in {"ready", "draining", "unavailable"} and (
            not isinstance(roster, dict) or roster.get("status") not in {"shutdown", "closed"}
        )
    except Exception:
        return False


def _is_status_monitor_cmdline(cmdline: str) -> bool:
    tokens = cmdline.split()
    for index in range(len(tokens) - 2):
        if tokens[index] == "-m" and tokens[index + 1] == "agent_worktrees":
            return tokens[index + 2] == "status-monitor"
    return False


def _iter_status_monitor_pids() -> set[int]:
    if os.name == "nt":
        from . import windows_process_census

        return {
            item["pid"] for item in windows_process_census.snapshot()
            if _is_status_monitor_cmdline(item["command_line"])
        }
    argv = ["ps", "-eo", "pid=,args="]
    out = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False)  # noqa: S603
    if out.returncode != 0:
        raise OSError(f"native monitor process census failed (exit {out.returncode})")
    hits: set[int] = set()
    for line in (out.stdout or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid_s, cmdline = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if _is_status_monitor_cmdline(cmdline):
            hits.add(pid)
    return hits


def _reap_abandoned_passive(record: dict | None) -> dict:
    table = routing.read_table(routing_dir()) or {}
    active = table.get("active") if isinstance(table, dict) else None
    active_pid = active.get("pid") if isinstance(active, dict) else None

    def _pid_alive(pid: int) -> bool:
        return pid in _iter_status_monitor_pids()

    def _terminate(pid: int) -> bool:
        from zdd.diagnostics import terminate_pid_if_identity

        result = terminate_pid_if_identity(
            pid, (record or {}).get("new_process_start_time"), exit_timeout=5.0,
        )
        return all(
            result.get(key) is True for key in ("killed", "identity_verified", "exit_confirmed")
        )

    return breadcrumb.reap_abandoned_passive(
        routing_dir(),
        pid_alive=_pid_alive,
        terminate=_terminate,
        active_pid=int(active_pid) if active_pid else None,
        record=record,
        grace_seconds=0.0,
    )


def activate_after_update(
    *,
    runtime_python: str | None = None,
    health_timeout: float = _DEFAULT_HEALTH_TIMEOUT_S,
    drain_timeout: float = _DEFAULT_DRAIN_TIMEOUT_S,
    monitor_was_live: bool | None = None,
    cutover_guard: SingleInstance | None = None,
    spawned_handles: list[subprocess.Popen] | None = None,
    transition_custody: ExitStack | None = None,
) -> dict[str, object]:
    """Installer seam: cut over a live monitor after activating a new slot."""
    from . import status_monitor_reap_stale
    from . import status_monitor_runtime as smr

    if spawned_handles is not None:
        print("Monitor transition: validating owning guard", flush=True)
    if cutover_guard is not None:
        if (
            not isinstance(cutover_guard, SingleInstance)
            or not cutover_guard.held
            or cutover_guard.lock_path.resolve() != (routing_dir() / "cutover.lock").resolve()
        ):
            raise ValueError("Installer cutover requires the held guard for this runtime root")

    summary: dict[str, object] = {
        "enabled": smr._status_monitor_enabled(),
        "action": "noop",
    }
    if not summary["enabled"]:
        summary["reason"] = "disabled"
        return summary
    refusal = breadcrumb.recovery_refusal(
        routing_dir(), breadcrumb.read_breadcrumb(routing_dir()),
    )
    if refusal is not None:
        summary.update(action="refused", reason=refusal, result={"ok": False, "error": refusal})
        return summary
    monitor_was_live = _monitor_lock_is_live() if monitor_was_live is None else bool(monitor_was_live)
    restart_arguments = {}
    if runtime_python is not None:
        restart_arguments["runtime_python"] = runtime_python
    if spawned_handles is not None:
        restart_arguments["spawned_handles"] = spawned_handles
    lock_live = _monitor_lock_is_live()
    if not lock_live:
        try:
            routed_live = routed_monitor_identity() is not None
        except (OSError, ValueError, ConnectionError):
            summary.update(
                action="refused", reason="routed-monitor-unverified",
                result={"ok": False, "error": "Routed monitor identity could not be verified"},
            )
            return summary
    else:
        routed_live = False
    if not lock_live and not routed_live:
        if monitor_was_live:
            summary["action"] = "restart"
            summary["restart"] = smr._restart_status_monitor(**restart_arguments)
            if cutover_guard is None or spawned_handles is None:
                status_monitor_reap_stale.schedule_delayed_daemon_health_reap()
            return summary
        summary["reason"] = "no-live-monitor"
        return summary
    active_url = _monitor_control_url_from_route()
    if active_url is None:
        if monitor_was_live:
            summary["action"] = "restart"
            summary["restart"] = smr._restart_status_monitor(**restart_arguments)
            if cutover_guard is None or spawned_handles is None:
                status_monitor_reap_stale.schedule_delayed_daemon_health_reap()
            return summary
        summary["reason"] = "no-routed-monitor"
        return summary

    try:
        lease = cutover_guard if cutover_guard is not None else _acquire_cutover_lock(routing_dir())
    except AlreadyRunningError as exc:
        summary["reason"] = f"cutover-busy:{exc.holder_pid or 'unknown'}"
        return summary
    py = runtime_python or sys.executable
    make_client = _make_client
    if transition_custody is not None:
        def make_client(base_url: str) -> ControlClient:
            client = _make_client(base_url)
            client.transition_custody = transition_custody
            return client
    try:
        if spawned_handles is not None:
            print("Monitor transition: checking prior recovery", flush=True)
        pre_recovery = breadcrumb.read_breadcrumb(routing_dir())
        summary["recovery"] = breadcrumb.recover_stale_cutover(
            routing_dir(),
            make_client,
            health_check=_health_check,
        )
        if summary["recovery"].get("refused"):
            reason = breadcrumb.refusal_reason(summary["recovery"])
            summary.update(action="refused", reason=reason, result={"ok": False, "error": reason})
            return summary
        if spawned_handles is not None:
            print("Monitor transition: checking abandoned passive custody", flush=True)
        summary["passive_reap"] = _reap_abandoned_passive(pre_recovery)
        if summary["passive_reap"].get("refused"):
            reason = breadcrumb.refusal_reason(summary["passive_reap"])
            summary.update(action="refused", reason=reason, result={"ok": False, "error": reason})
            return summary
        def spawn(port: int):
            handle = spawn_passive(py, port=port)
            if spawned_handles is not None:
                spawned_handles.append(handle)
                print(f"Monitor transition: passive spawned pid={getattr(handle, 'pid', None)} port={port}", flush=True)
            return handle

        orch = CutoverOrchestrator(
            routing_dir(),
            bind=_BIND,
            version=None,
            spawn_passive=spawn,
            health_check=_health_check,
            predecessor_health_check=_predecessor_health_check,
            make_client=make_client,
            pick_free_port=pick_free_port,
        )
        if spawned_handles is not None:
            print("Monitor transition: entering native cutover", flush=True)
        result = orch.run(
            health_timeout=health_timeout,
            drain_timeout=drain_timeout,
            force=False,
        )
        summary["action"] = "cutover"
        summary["result"] = result.to_dict()
    finally:
        try:
            if cutover_guard is None:
                lease.release()
        finally:
            # Legacy callers require the async backstop, even on an unexpected
            # exception escaping stale recovery, orchestration, or the
            # lease release itself above (copilot-extensions#5453): the
            # installer wrapper suppresses a raised exception from this
            # whole function as a non-fatal failure, so without this
            # living in its own nested `finally` (sequential with
            # `lease.release()` would let a release failure itself skip
            # it) any such exception would skip scheduling the one thing
            # meant to heal an ambiguous daemon state left behind by that
            # very failure. An owner supplying both the guard and child
            # handles instead settles those processes under retained custody.
            if cutover_guard is None or spawned_handles is None:
                status_monitor_reap_stale.schedule_delayed_daemon_health_reap()
    return summary


def installer_after_update() -> int:
    """Non-fatal installer wrapper that prints a short cutover summary."""
    summary = activate_after_update(
        monitor_was_live=os.environ.get("AGENT_WORKTREES_MONITOR_WAS_LIVE") == "1",
    )
    action = str(summary.get("action") or "noop")
    if not summary.get("enabled"):
        print("status-monitor disabled; skipped")
        return 0
    if action == "noop":
        print(f"status-monitor {summary.get('reason', 'noop')}; left as-is")
        return 0
    if action == "restart":
        restart = summary.get("restart")
        if isinstance(restart, dict):
            if restart.get("already_current"):
                print("no routed live monitor; current monitor already owns the host")
            elif restart.get("spawned"):
                print("no routed live monitor; restarted the current monitor")
            else:
                print("no routed live monitor; restart attempt did not spawn a monitor")
        return 0
    result = summary.get("result") if isinstance(summary.get("result"), dict) else {}
    if result.get("ok"):
        print(f"cut over live monitor to port {result.get('new_port')}")
    else:
        print(f"cutover failed; rollback kept serving state ({result.get('error')})")
    return 0


def _acquire_cutover_lock(
    lock_root: Path,
    *,
    timeout_s: float = _CUTOVER_LOCK_TIMEOUT_S,
    poll_s: float = 0.2,
) -> SingleInstance:
    lease = SingleInstance(lock_root, service="status-monitor-cutover", lock_name="cutover.lock")
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            lease.acquire()
            return lease
        except AlreadyRunningError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_s)


def monitor_live_now() -> bool:
    return _monitor_lock_is_live()


def live_endpoint() -> routing.Endpoint | None:
    return routing.read_active_endpoint(routing_dir(), verify_listener=False)


def clear_route_if_owner(pid: int) -> bool:
    return routing.clear_if_owner(routing_dir(), pid=pid)


def publish_route(port: int, *, pid: int, version: str | None) -> routing.Endpoint:
    return routing.publish_active(
        routing_dir(),
        bind=_BIND,
        port=port,
        pid=pid,
        version=version,
        demote_existing=True,
    )


def active_generation_for_pid(pid: int) -> int | None:
    table = routing.read_table(routing_dir())
    raw = table.get("active") if isinstance(table, dict) else None
    endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
    if endpoint is None or endpoint.pid != pid:
        return None
    return int(endpoint.generation)
