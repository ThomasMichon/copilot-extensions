"""Zero-downtime cutover helpers for the resident Worktree Manager mux-daemon."""

from __future__ import annotations

import os
import secrets
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent_procutil import detached_kwargs, windowless_daemon_kwargs
from zdd import breadcrumb, diagnostics, routing
from zdd.cutover import CutoverOrchestrator

from . import mux_daemon_process
from .mux_mapping_registry import _try_lock_file_once, _unlock_file
from .self_install import default_root

_BIND = "127.0.0.1"
_READ_TIMEOUT_S = 5.0
_CUTOVER_LOCK_TIMEOUT_S = 300.0
_ROUTING_DIRNAME = "mux-daemon-routing"
_TOKEN_FILENAME = "mux-daemon.token"
_HEALTH_KIND = "mux-cutover-health-v1"
_DRAIN_KIND = "mux-cutover-drain-v1"
_UNDRAIN_KIND = "mux-cutover-undrain-v1"
_SHUTDOWN_KIND = "mux-cutover-shutdown-v1"
_ADOPT_KIND = "mux-cutover-adopt-v1"


def routing_dir(root: Path | None = None) -> Path:
    return (root if root is not None else default_root()) / _ROUTING_DIRNAME


def control_token_path(root: Path | None = None) -> Path:
    return (root if root is not None else default_root()) / _TOKEN_FILENAME


def load_or_create_control_token(root: Path | None = None) -> str:
    path = control_token_path(root)
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


def pick_free_port(bind: str = _BIND) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((bind, 0))
        return int(sock.getsockname()[1])


def routed_endpoint(root: Path | None = None) -> routing.Endpoint | None:
    return routing.read_active_endpoint(routing_dir(root))


def live_endpoint_from_lock(lock_data: dict | None) -> tuple[str, int] | None:
    if not isinstance(lock_data, dict):
        return None
    endpoint = lock_data.get("manager_mux_endpoint")
    if not isinstance(endpoint, str):
        return None
    host, _, port_s = endpoint.partition(":")
    if not host or not port_s:
        return None
    try:
        port = int(port_s)
    except ValueError:
        return None
    if not 0 < port < 65536:
        return None
    return host, port


def publish_route(port: int, *, root: Path | None = None, pid: int, version: str | None) -> routing.Endpoint:
    return routing.publish_active(
        routing_dir(root),
        bind=_BIND,
        port=port,
        pid=pid,
        version=version,
        demote_existing=True,
    )


def clear_route_if_owner(pid: int, *, root: Path | None = None) -> bool:
    return routing.clear_if_owner(routing_dir(root), pid=pid)


def active_generation_for_pid(pid: int, *, root: Path | None = None) -> int | None:
    table = routing.read_table(routing_dir(root))
    raw = table.get("active") if isinstance(table, dict) else None
    endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
    if endpoint is None or endpoint.pid != pid:
        return None
    return int(endpoint.generation)


def is_superseded(root: Path | None, my_pid: int, my_generation: int) -> bool:
    table = routing.read_table(routing_dir(root))
    raw = table.get("active") if isinstance(table, dict) else None
    endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
    if endpoint is None or endpoint.pid == my_pid or endpoint.generation <= my_generation:
        return False
    host = endpoint.client_host
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            return sock.connect_ex((host, endpoint.port)) == 0
    except OSError:
        return False


def _cmdline_root(cmdline: str) -> Path | None:
    for token in shlex.split(cmdline, posix=os.name != "nt"):
        token = token.strip("\"'")
        if token.startswith("--root="):
            return Path(token.split("=", 1)[1]).expanduser().resolve()
    return None


def _pid_matches_root(pid: int, *, root: Path) -> bool:
    resolved_root = root.expanduser().resolve()
    if os.name == "nt":
        argv = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" | "
            "ForEach-Object { $_.CommandLine }" % pid,
        ]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)  # noqa: S603
        cmdline = (out.stdout or "").strip()
        if not cmdline:
            return False
        recorded = _cmdline_root(cmdline)
        return recorded == resolved_root or (
            recorded is None and resolved_root == default_root().expanduser().resolve()
        )
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.is_file():
        try:
            parts = [part for part in proc_cmdline.read_text("utf-8").split("\0") if part]
        except OSError:
            parts = []
        for token in parts:
            if token.startswith("--root="):
                recorded = Path(token.split("=", 1)[1]).expanduser().resolve()
                return recorded == resolved_root
        return resolved_root == default_root().expanduser().resolve()
    out = subprocess.run(
        ["ps", "-p", str(pid), "-o", "args="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )  # noqa: S603
    cmdline = (out.stdout or "").strip()
    if not cmdline:
        return False
    recorded = _cmdline_root(cmdline)
    return recorded == resolved_root or (
        recorded is None and resolved_root == default_root().expanduser().resolve()
    )


class ControlClient:
    def __init__(self, base_url: str, *, root: Path | None = None, timeout: float = 5.0):
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "") or not parsed.hostname or parsed.port is None:
            raise ValueError(f"invalid mux-daemon cutover URL: {base_url!r}")
        self.host = parsed.hostname
        self.port = int(parsed.port)
        self.base_url = base_url
        self.token = load_or_create_control_token(root)
        self.root = root if root is not None else default_root()
        self.timeout = timeout

    def _request(self, kind: str, payload: dict | None = None, *, key: str = "control") -> dict:
        from work_coalescing_singleton import client as wcs_client

        client_id = wcs_client.new_client_id()
        try:
            return wcs_client.request(
                self.host,
                self.port,
                self.token,
                kind=kind,
                key=key,
                payload=payload or {},
                request_deadline_s=self.timeout,
                client_id=client_id,
            )
        finally:
            wcs_client.release(
                self.host, self.port, self.token, client_id, timeout=self.timeout
            )

    def health(self) -> dict[str, Any]:
        # A request-unique key (never the shared "control" default) so
        # concurrent health probes are never coalesced into a single
        # `_compute` execution: `daemons status`'s concurrent-load
        # accounting increments once per real wire request, and
        # coalescing two overlapping probes into one computation would
        # hide the second one from that accounting entirely. Health is
        # cheap and idempotent, so there is no benefit to coalescing it
        # the way drain/undrain/shutdown/adopt still are.
        return self._request(_HEALTH_KIND, key=f"{_HEALTH_KIND}:{secrets.token_hex(8)}")

    def drain(self, *, timeout: float, poll: float, force: bool) -> dict[str, Any]:
        result = self._request(
            _DRAIN_KIND,
            {"timeout": timeout, "poll": poll, "force": force},
        )
        if result.get("drained"):
            active = routed_endpoint(self.root)
            if active is not None and active.base_url != self.base_url:
                ControlClient(active.base_url, root=self.root, timeout=self.timeout).adopt_relay()
        return result

    def undrain(self) -> dict[str, Any]:
        return self._request(_UNDRAIN_KIND)

    def shutdown(self) -> dict[str, Any]:
        return self._request(_SHUTDOWN_KIND)

    def adopt_relay(self) -> dict[str, Any]:
        return self._request(_ADOPT_KIND)


def health_kind() -> str:
    return _HEALTH_KIND


def drain_kind() -> str:
    return _DRAIN_KIND


def undrain_kind() -> str:
    return _UNDRAIN_KIND


def shutdown_kind() -> str:
    return _SHUTDOWN_KIND


def adopt_kind() -> str:
    return _ADOPT_KIND


def spawn_passive(slot: Path, *, root: Path, port: int):
    """Spawn a passive mux-daemon candidate serving ``slot``'s payload.

    Cross-version process isolation invariant (see
    ``docs/patterns/graceful-daemon-cutover.md``): a spawned child's ``cwd``
    must never be pinned inside the version slot it serves. ``slot`` is
    reachable solely as a ``PYTHONPATH`` entry below -- the child's working
    directory is ``root`` (the stable install root, never itself replaced)
    instead. Pinning ``cwd`` to ``slot`` was the direct cause of
    copilot-extensions#4999/#5053: on Windows, a process's cwd holds an open
    directory handle for as long as the process lives, so any later
    self-install targeting that exact slot (a retry, or a second payload at
    the same version) collided with `shutil.rmtree(slot)` for as long as this
    passive (or its abandoned remains) kept running.
    """
    pythonpath = [str(slot / "src")]
    libs_dir = slot / "libs"
    if libs_dir.is_dir():
        for child in sorted(libs_dir.iterdir()):
            src = child / "src"
            if src.is_dir():
                pythonpath.append(str(src))
    python, extra_env = mux_daemon_process.direct_daemon_python(sys.executable)
    argv = [
        python,
        "-m",
        "worktree_manager",
        "mux-daemon",
        "run",
        f"--root={root}",
        f"--listen-port={port}",
        "--passive",
    ]
    env = mux_daemon_process.scrub_session_credentials(dict(os.environ))
    env.update(extra_env)
    kwargs: dict[str, object] = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
    }
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        pythonpath + ([existing] if existing else [])
    )
    kwargs.update(windowless_daemon_kwargs(breakaway=True))
    if os.name != "nt":
        kwargs.update(detached_kwargs())
    return subprocess.Popen(argv, **kwargs)  # noqa: S603


def _health_check(host: str, port: int, *, root: Path) -> bool:
    try:
        status = ControlClient(f"http://{routing.format_authority(host, port)}", root=root).health()
        return status.get("status") != "draining"
    except Exception:
        return False


def _make_client(base_url: str, *, root: Path) -> ControlClient:
    return ControlClient(base_url, root=root, timeout=65.0)


def _is_mux_daemon_cmdline(cmdline: str) -> bool:
    tokens = cmdline.split()
    for index in range(len(tokens) - 2):
        if tokens[index] == "-m" and tokens[index + 1] == "worktree_manager":
            return (
                tokens[index + 2] == "mux-daemon"
                and "run" in tokens[index + 3:]
            )
    return False


def _iter_mux_daemon_pids() -> set[int]:
    if os.name == "nt":
        argv = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }",
        ]
        out = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)  # noqa: S603
        hits: set[int] = set()
        for line in (out.stdout or "").splitlines():
            if "\t" not in line:
                continue
            pid_s, cmdline = line.split("\t", 1)
            try:
                pid = int(pid_s.strip())
            except ValueError:
                continue
            if _is_mux_daemon_cmdline(cmdline):
                hits.add(pid)
        return hits
    out = subprocess.run(  # noqa: S603
        ["ps", "-eo", "pid=,args="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        env=_ps_env_without_width_override(),
    )
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
        if _is_mux_daemon_cmdline(cmdline):
            hits.add(pid)
    return hits


def _ps_env_without_width_override() -> dict[str, str]:
    """``os.environ`` with ``COLUMNS``/``LINES`` stripped.

    ``ps -eo ...,args=`` truncates the unbounded ``args`` field to terminal
    width -- and several ``ps`` implementations honor an inherited
    ``$COLUMNS`` for this even when stdout is a pipe, not a real terminal
    (confirmed: a long, genuine mux-daemon command line gets cut off mid-flag
    under ``COLUMNS=80``, well before the ``-m worktree_manager mux-daemon
    run`` tokens ``_is_mux_daemon_cmdline`` matches against). Some CI runners
    export ``COLUMNS`` into every step's environment, which silently broke
    real-process identity matching here -- not just the flaky-looking local
    test, but the actual cutover/termination safety check in production
    whenever a long enough installed path landed under a narrow inherited
    ``COLUMNS``. Stripping both before calling ``ps`` makes this reliable
    regardless of what the parent process's terminal/environment happened to
    export.
    """
    env = dict(os.environ)
    env.pop("COLUMNS", None)
    env.pop("LINES", None)
    return env


def _terminate_mux_daemon_pid(pid: int, *, root: Path) -> bool:
    """Terminate ``pid`` after verifying it is a mux-daemon bound to ``root``.

    This is the ONE legitimate PID-kill fallback this module uses: the
    ``_legacy_lock_cutover`` caller only ever reaches here for a resident
    that predates the cutover protocol entirely (no routing-table entry,
    so there is no IPC endpoint to ask it to drain/self-retire -- see the
    cross-version isolation invariant in
    ``docs/patterns/graceful-daemon-cutover.md``). Every protocol-aware
    resident is instead asked to retire itself over IPC
    (``ControlClient.drain``/``shutdown``); this function is never called
    for one.

    Routed entirely through ``zdd.diagnostics.terminate_pid_if_identity``
    -- the shared, identity-bound termination primitive -- rather than a
    bare ``os.kill``/``taskkill`` by PID. A process-start-time token is
    captured immediately after the cmdline/root identity check above, and
    the kill itself is issued through an OS object (a Windows handle /
    POSIX pidfd) bound to that exact ``(pid, start_time)`` pair, so a PID
    the OS has since reused for an unrelated process can never be signaled
    in its place (closes copilot-extensions#5006 for this call site).
    """
    if pid not in _iter_mux_daemon_pids() or not _pid_matches_root(pid, root=root):
        return False
    start_time = diagnostics.process_start_time(pid)
    if start_time is None:
        return False
    return bool(diagnostics.terminate_pid_if_identity(pid, start_time).get("killed"))


def _reap_abandoned_passive(root: Path, record: dict | None) -> dict:
    table = routing.read_table(routing_dir(root)) or {}
    active = table.get("active") if isinstance(table, dict) else None
    active_pid = active.get("pid") if isinstance(active, dict) else None
    return breadcrumb.reap_abandoned_passive(
        routing_dir(root),
        pid_alive=lambda pid: pid in _iter_mux_daemon_pids(),
        terminate=lambda pid: _terminate_mux_daemon_pid(pid, root=root),
        active_pid=int(active_pid) if active_pid else None,
        record=record,
        grace_seconds=0.0,
    )


def _cutover_lock_path(root: Path) -> Path:
    return routing_dir(root) / "cutover.lock"


class _CutoverLease:
    def __init__(self, handle):
        self._handle = handle

    def release(self) -> None:
        try:
            _unlock_file(self._handle)
        except OSError:
            pass
        self._handle.close()


def _acquire_cutover_lock(root: Path, *, timeout_s: float = _CUTOVER_LOCK_TIMEOUT_S, poll_s: float = 0.2):
    path = _cutover_lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    while True:
        handle = open(path, "a+b")
        try:
            if _try_lock_file_once(handle):
                return _CutoverLease(handle)
            handle.close()
            if time.monotonic() >= deadline:
                raise TimeoutError("mux-daemon cutover lock busy")
            time.sleep(poll_s)
        except OSError:
            handle.close()
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_s)


def _lock_data_is_live(lock_data: dict | None) -> bool:
    endpoint = live_endpoint_from_lock(lock_data)
    if endpoint is None:
        return False
    host, port = endpoint
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def _await_health(host: str, port: int, *, root: Path, timeout: float, poll: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _health_check(host, port, root=root):
            return True
        time.sleep(poll)
    return _health_check(host, port, root=root)


def _legacy_lock_cutover(
    *,
    root: Path,
    slot: Path,
    version: str,
    lock_data: dict,
    health_timeout: float,
) -> dict[str, object]:
    old_endpoint = live_endpoint_from_lock(lock_data)
    old_pid = lock_data.get("pid")
    summary: dict[str, object] = {"action": "legacy-cutover"}
    if old_endpoint is None or not isinstance(old_pid, int):
        summary["reason"] = "legacy-lock-invalid"
        return summary
    new_port = pick_free_port()
    handle = spawn_passive(slot, root=root, port=new_port)
    if not _await_health(_BIND, new_port, root=root, timeout=health_timeout):
        handle.terminate()
        summary["result"] = {"ok": False, "new_port": new_port, "error": "health-timeout"}
        return summary
    publish_route(new_port, root=root, pid=getattr(handle, "pid", None), version=version)
    retired = _terminate_mux_daemon_pid(old_pid, root=root)
    if not retired:
        mux_endpoint = getattr(handle, "pid", None)
        if isinstance(mux_endpoint, int):
            clear_route_if_owner(mux_endpoint, root=root)
        handle.terminate()
    else:
        ControlClient(
            f"http://{routing.format_authority(_BIND, new_port)}",
            root=root,
        ).adopt_relay()
    summary["result"] = {
        "ok": retired,
        "legacy": True,
        "old_pid": old_pid,
        "new_port": new_port,
        "retired_old": retired,
    }
    if not retired:
        summary["result"]["error"] = "legacy predecessor could not be retired"
    return summary


def _already_at_version(route: routing.Endpoint | None, version: str, *, root: Path) -> bool:
    """True if ``route`` is already serving ``version`` and is actually healthy.

    A cutover attempt is only ever necessary on a genuine version change; a
    caller that reconciles on every session launch (self_update runs at the
    start of each one) will otherwise call in with the *same* target version
    far more often than the underlying payload actually changes -- a rapid-
    development harness in particular sees both new mux sessions and new
    published versions "fairly often", and this is the fast path that keeps
    the two from compounding into a cutover on every launch (#5344).
    """
    return (
        route is not None
        and route.version == version
        and _health_check(_BIND, route.port, root=root)
    )


def _stale_cutover_in_progress(root: Path) -> bool:
    """True if the durable breadcrumb marks an aborted prior cutover.

    Only used to gate the *lock-free* pre-check below: it cannot safely run
    recovery itself (no lock held), so while a cutover looks aborted it must
    fall through to the locked path, where recovery always runs before any
    no-op decision is made.
    """
    return breadcrumb.is_stale(breadcrumb.read_breadcrumb(routing_dir(root)))


def activate_after_update(
    *,
    root: Path | None = None,
    slot: Path,
    version: str,
    health_timeout: float = 60.0,
    drain_timeout: float = 30.0,
) -> dict[str, object]:
    resolved_root = root if root is not None else default_root()
    summary: dict[str, object] = {"action": "noop", "root": str(resolved_root)}

    from . import mux_daemon

    # Fast, lock-free skip: don't even contend the cutover lock for a no-op
    # reconciliation call -- the common case when sessions launch often. Only
    # while there is no stale breadcrumb: a stale one must always fall
    # through to the locked path below, where recovery/reaping runs before
    # any no-op decision (a crashed prior cutover must never be masked by
    # "the target version is already active" -- the same abandoned-passive
    # gap `zdd.breadcrumb`'s module docstring describes; #5344 follow-up).
    pre_route = routed_endpoint(resolved_root)
    if not _stale_cutover_in_progress(resolved_root) and _already_at_version(
        pre_route, version, root=resolved_root
    ):
        summary["reason"] = "already-active-version"
        summary["route"] = {
            "port": pre_route.port, "pid": pre_route.pid, "version": pre_route.version,
        }
        return summary

    lease = _acquire_cutover_lock(resolved_root)
    try:
        load_or_create_control_token(resolved_root)
        lock_data = mux_daemon.read_lock_data(mux_daemon.lock_path(resolved_root))
        route = routed_endpoint(resolved_root)
        if route is None:
            if not _lock_data_is_live(lock_data):
                summary["reason"] = "no-live-daemon"
                return summary
            return _legacy_lock_cutover(
                root=resolved_root,
                slot=slot,
                version=version,
                lock_data=lock_data or {},
                health_timeout=health_timeout,
            )
        # Always run stale-cutover recovery and abandoned-passive reaping
        # before deciding anything -- including the same-version no-op check
        # right below -- so a crashed prior cutover's stranded old daemon or
        # abandoned passive is never masked by an early "already active"
        # return.
        pre_recovery = breadcrumb.read_breadcrumb(routing_dir(resolved_root))
        summary["recovery"] = breadcrumb.recover_stale_cutover(
            routing_dir(resolved_root),
            lambda base_url: _make_client(base_url, root=resolved_root),
            health_check=lambda host, port: _health_check(host, port, root=resolved_root),
        )
        summary["passive_reap"] = _reap_abandoned_passive(resolved_root, pre_recovery)

        # Re-check now, under the lock and after recovery/reap: another
        # caller may have already cut over to this exact version while we
        # waited for the lock, or the recovery above may just have healed
        # the last aborted attempt back onto it.
        route = routed_endpoint(resolved_root)
        if _already_at_version(route, version, root=resolved_root):
            summary["reason"] = "already-active-version"
            summary["route"] = {"port": route.port, "pid": route.pid, "version": route.version}
            return summary

        orch = CutoverOrchestrator(
            routing_dir(resolved_root),
            bind=_BIND,
            version=version,
            spawn_passive=lambda port: spawn_passive(slot, root=resolved_root, port=port),
            health_check=lambda host, port: _health_check(host, port, root=resolved_root),
            make_client=lambda base_url: _make_client(base_url, root=resolved_root),
            pick_free_port=pick_free_port,
            service="worktree-manager-mux-daemon",
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
