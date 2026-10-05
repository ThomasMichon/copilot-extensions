"""Installer-driven status-monitor cutover helpers."""

from __future__ import annotations

import json
import os
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from agent_procutil import detached_kwargs, windowless_python, windowless_python_env
from single_instance_lease import AlreadyRunningError, SingleInstance
from zdd import breadcrumb
from zdd import routing
from zdd.cutover import CutoverOrchestrator

from . import locks, procs

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
            payload = request.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            result = self.server.owner.handle(action, payload)  # type: ignore[attr-defined]
            if not isinstance(result, dict):
                result = {}
            self.wfile.write(
                json.dumps({"version": 1, "result": result}, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
        except Exception:
            return


class ControlServer:
    """Loopback control plane for the resident status-monitor."""

    def __init__(
        self,
        handle: Callable[[str, dict], dict],
        *,
        port: int | None = None,
        token: str | None = None,
    ):
        self._handle = handle
        self._active_handlers = 0
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


class ControlClient:
    """Small JSON-line client for the status-monitor control surface."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        config_dir: Path | None = None,
        timeout: float = 5.0,
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

    def _request(self, action: str, payload: dict | None = None) -> dict:
        body = json.dumps(
            {"version": 1, "token": self.token, "action": action, "payload": payload or {}},
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(self.timeout)
            sock.sendall(body)
            sock.shutdown(socket.SHUT_WR)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        response = json.loads(buf.decode("utf-8"))
        result = response.get("result")
        if not isinstance(result, dict):
            raise ValueError("status-monitor control response missing result")
        return result

    def health(self) -> dict:
        return self._request("health")

    def drain(self, *, timeout: float, poll: float, force: bool) -> dict:
        if self.config_dir is not None:
            active = routing.read_active_endpoint(self.config_dir, verify_listener=False)
            if active is not None and active.base_url != self.base_url:
                promoted = ControlClient(
                    active.base_url,
                    token=self.token,
                    config_dir=self.config_dir,
                    timeout=self.timeout,
                ).promote()
                if not promoted.get("adopted"):
                    raise ValueError("status-monitor successor refused promotion")
        return self._request(
            "drain",
            {"timeout": timeout, "poll": poll, "force": force},
        )

    def undrain(self) -> dict:
        return self._request("undrain")

    def shutdown(self) -> dict:
        return self._request("shutdown")

    def adopt_relay(self) -> dict:
        return self.promote()

    def promote(self) -> dict:
        return self._request("promote")


def spawn_passive(runtime_python: str, *, port: int):
    from . import status_monitor_runtime as smr
    core = smr._core()

    child_env = smr._daemon_environment()
    child_env["AGENT_WORKTREES_STATUS_MONITOR_ROUTING_DIR"] = str(routing_dir())
    kwargs: dict[str, object] = {
        "env": child_env,
        "cwd": smr._daemon_cwd(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    argv = [
        runtime_python,
        "-m",
        "agent_worktrees",
        "status-monitor",
        "--passive",
        "--control-port",
        str(port),
    ]
    if os.name == "nt":
        kwargs.update(core.windowless_daemon_kwargs(breakaway=True))
    else:
        argv[0] = windowless_python(runtime_python)
        child_env.update(windowless_python_env(runtime_python))
        kwargs.update(detached_kwargs())
    return subprocess.Popen(argv, **kwargs)  # noqa: S603


def _monitor_control_url_from_route() -> str | None:
    endpoint = routing.read_active_endpoint(routing_dir())
    if endpoint is None:
        return None
    return endpoint.base_url


def _monitor_lock_is_live() -> bool:
    from . import status_monitor_runtime as smr

    return locks.lock_is_live(locks.read_lock(smr._monitor_lock_path()))


def _health_check(host: str, port: int) -> bool:
    try:
        status = ControlClient(f"http://{host}:{port}").health()
        return status.get("status") != "draining"
    except Exception:
        return False


def _make_client(base_url: str) -> ControlClient:
    return ControlClient(base_url, config_dir=routing_dir(), timeout=65.0)


def _is_status_monitor_cmdline(cmdline: str) -> bool:
    tokens = cmdline.split()
    for index in range(len(tokens) - 2):
        if tokens[index] == "-m" and tokens[index + 1] == "agent_worktrees":
            return tokens[index + 2] == "status-monitor"
    return False


def _iter_status_monitor_pids() -> set[int]:
    if os.name == "nt":
        argv = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }",
        ]
        out = subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, timeout=10, check=False
        )
        hits: set[int] = set()
        for line in (out.stdout or "").splitlines():
            if "\t" not in line:
                continue
            pid_s, cmdline = line.split("\t", 1)
            try:
                pid = int(pid_s.strip())
            except ValueError:
                continue
            if _is_status_monitor_cmdline(cmdline):
                hits.add(pid)
        return hits
    argv = ["ps", "-eo", "pid=,args="]
    out = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False)  # noqa: S603
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
        return bool(
            procs.terminate_pid_if_identity(pid, locks.process_start_time(pid)).get("killed")
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
) -> dict[str, object]:
    """Installer seam: cut over a live monitor after activating a new slot."""
    from . import status_monitor_runtime as smr

    summary: dict[str, object] = {
        "enabled": smr._status_monitor_enabled(),
        "action": "noop",
    }
    if not summary["enabled"]:
        summary["reason"] = "disabled"
        return summary
    monitor_was_live = _monitor_lock_is_live() if monitor_was_live is None else bool(monitor_was_live)
    if not _monitor_lock_is_live():
        if monitor_was_live:
            summary["action"] = "restart"
            summary["restart"] = smr._restart_status_monitor()
            return summary
        summary["reason"] = "no-live-monitor"
        return summary
    active_url = _monitor_control_url_from_route()
    if active_url is None:
        if monitor_was_live:
            summary["action"] = "restart"
            summary["restart"] = smr._restart_status_monitor()
            return summary
        summary["reason"] = "no-routed-monitor"
        return summary

    try:
        lease = _acquire_cutover_lock(routing_dir())
    except AlreadyRunningError as exc:
        summary["reason"] = f"cutover-busy:{exc.holder_pid or 'unknown'}"
        return summary
    py = runtime_python or sys.executable
    try:
        pre_recovery = breadcrumb.read_breadcrumb(routing_dir())
        summary["recovery"] = breadcrumb.recover_stale_cutover(
            routing_dir(),
            _make_client,
            health_check=_health_check,
        )
        summary["passive_reap"] = _reap_abandoned_passive(pre_recovery)
        orch = CutoverOrchestrator(
            routing_dir(),
            bind=_BIND,
            version=None,
            spawn_passive=lambda port: spawn_passive(py, port=port),
            health_check=_health_check,
            make_client=_make_client,
            pick_free_port=pick_free_port,
        )
        result = orch.run(
            health_timeout=health_timeout,
            drain_timeout=drain_timeout,
            force=False,
        )
    finally:
        lease.release()
    summary["action"] = "cutover"
    summary["result"] = result.to_dict()
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
