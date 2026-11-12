"""Zero-downtime cutover for ``agent-mcp serve`` (docs/patterns/graceful-daemon-
cutover.md, ``libs/zdd``).

The problem this closes: ``agent-mcp materialize``/``call`` and the multiplexed
``bridge``/``forward`` attach path all resolve the resident ``serve`` daemon
through a *fixed*, client-facing handle (an AF_UNIX socket path on POSIX, a
``<handle>.endpoint`` sidecar on Windows -- see :mod:`agent_mcp.sockio` /
:mod:`agent_mcp.ipc`). Before this module, replacing that daemon with a new
version meant stopping the old one and starting the new one -- a window,
however brief, where the fixed handle resolves to nothing and every call/attach
in that window has no live instance to reach. This module makes replacing the
daemon a **cutover** instead: stand the new version up *beside* the old one,
health-gate it, flip the fixed handle to it, drain the old one (refuse new
``attach`` sessions; already-attached ones and in-flight ``call``/``list``
requests are unaffected), then retire it -- so the fixed handle always
resolves to a live daemon.

Two separate planes, deliberately:

* **Data plane** -- the daemon's existing AF_UNIX/TCP transport, unchanged.
  Real MCP traffic never touches this module's code.
* **Control plane** -- a small, always-on, loopback-TCP-only listener every
  ``serve`` daemon (passive or promoted) binds purely for lifecycle signaling
  (``ping``/``drain``/``undrain``/``shutdown``), published to the shared
  ``zdd`` routing table (``libs/zdd``) so this orchestrator can find *any*
  running generation, including one it did not spawn.

Installed application callbacks start the selected physical generation
independently of its predecessor. The outgoing source-aware daemon retires
itself only after that exact replacement owns the ready data handle.
The legacy cutover protocol below retains its explicit bootstrap boundary
for daemons without a control channel.
"""

from __future__ import annotations

from agent_procutil.daemon_instances import DaemonInstanceSpec

import json
import os
import secrets
import socket as _socket
import stat
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ControlError(OSError):
    """The control channel could not be reached or gave a malformed reply."""


def control_request(host: str, port: int, token: str, req: dict,
                    *, timeout: float = 5.0) -> dict:
    """Send one line-JSON op to a daemon's control channel; return its reply.

    Synchronous + stdlib-only: the ``zdd.cutover.CutoverOrchestrator`` callbacks
    (``health_check``, and every ``_Client`` method) run in a plain sync
    context, not inside the daemon's own asyncio loop.
    """
    payload = dict(req)
    payload["token"] = token
    try:
        with _socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall((json.dumps(payload) + "\n").encode())
            sock.settimeout(timeout)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
    except OSError as exc:
        raise ControlError(f"control channel unreachable at {host}:{port}: {exc}") from exc
    if not buf:
        raise ControlError(f"control channel at {host}:{port} closed with no reply")
    try:
        resp = json.loads(buf.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ControlError(f"malformed control reply from {host}:{port}: {exc}") from exc
    if not isinstance(resp, dict):
        raise ControlError(f"control reply from {host}:{port} was not an object")
    return resp


def pick_free_port() -> int:
    """Bind an ephemeral loopback port, release it, and return the number.

    A small race exists between release and the real bind (another process
    could claim it first) -- acceptable here since a spawn failure just fails
    the cutover cleanly (pre-commit, so it rolls back) rather than corrupting
    state.
    """
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


@dataclass
class _CutoverContext:
    """Everything the injected callbacks need, resolved once up front."""

    data_root: Path
    new_socket_path: Path
    new_log_path: Path
    new_control_token: str
    old_control_token: str | None
    token_by_port: dict[int, str]
    config_home: Path | None = None
    fixed_socket: Path | None = None


def spawn_passive_daemon(ctx: _CutoverContext, control_port: int) -> subprocess.Popen:
    """``zdd`` ``spawn_passive`` callback: launch the installed code as a
    passive ``serve`` instance on its own data socket + this control port.

    Launches through the shared resident-daemon primitive
    ``agent_procutil.spawn_daemon`` (the same one ``forward._spawn_serve_host``
    uses): ``windowless_python()`` avoids a Windows venv ``python.exe`` re-exec
    flashing a fresh console, and ``spawn_daemon`` fully detaches the child,
    breaks it away from a kill-on-close Job around this short-lived cutover
    process (an updater's, for instance; contained test mode never requests
    breakaway), runs it from the stable
    ``AGENT_MCP_HOME`` data root rather than the caller's cwd, and appends its
    stdout/stderr to the generation's own log file. Without that log, a POSIX
    child would inherit whatever fds the launcher held open, and a daemon
    holding such a pipe open means the launcher's pipe never sees EOF (the
    class of bug fixed in agent-bridge's ``spawn_passive``,
    copilot-extensions#2200/#2201). A Job that refuses breakaway raises
    ``DaemonSpawnError`` here, failing the cutover before commit rather than
    promoting a generation that would die with the launching process.
    """
    from agent_procutil import spawn_daemon, windowless_python, windowless_python_env

    from .sockio import daemon_env_overrides

    # Anchor every path against THIS process's cwd before the spawn: the
    # child runs from the data root, so a relative AGENT_MCP_HOME-derived
    # socket/log path (or AGENT_MCP_HOME itself) would otherwise be resolved a
    # second time there and name ``home/home/...``.
    home = Path(ctx.config_home or ctx.data_root).resolve()
    socket_path = Path(os.path.abspath(ctx.new_socket_path))
    log_path = Path(os.path.abspath(ctx.new_log_path))
    python = sys.executable
    cmd = [windowless_python(python), "-m", "agent_mcp", "serve",
           "--socket", str(socket_path),
           "--passive", "--control-port", str(control_port)]
    env_overrides = daemon_env_overrides(home)
    if ctx.fixed_socket is not None:
        env_overrides["AGENT_MCP_SERVE_SOCKET"] = os.path.abspath(ctx.fixed_socket)
    env_overrides["AGENT_MCP_CONTROL_TOKEN"] = ctx.new_control_token
    env_overrides.update(windowless_python_env(python))
    home.mkdir(parents=True, exist_ok=True)
    proc = spawn_daemon(cmd, cwd=home, log_path=log_path,
                        instance=DaemonInstanceSpec(
                            "agent-mcp", socket_path.parent, Path(__file__).resolve(),
                        ),
                        env_overrides=env_overrides,
                        display=f"agent_mcp serve --passive --control-port {control_port}")
    ctx.token_by_port[control_port] = ctx.new_control_token
    return proc


def health_check(ctx: _CutoverContext, host: str, port: int) -> bool:
    """``zdd`` ``health_check`` callback: ping the new generation's control
    channel."""
    token = ctx.token_by_port.get(port)
    if token is None:
        return False
    try:
        resp = control_request(host, port, token, {"op": "ping"}, timeout=2)
    except ControlError:
        return False
    return bool(resp.get("ok") and resp.get("pong"))


class CutoverClient:
    """The ``zdd`` ``_Client`` protocol, implemented over agent-mcp's own
    control-channel line-JSON ops instead of HTTP -- the protocol is
    structural (duck-typed), so this satisfies it without a real HTTP server.
    """

    def __init__(self, base_url: str, ctx: _CutoverContext) -> None:
        u = urlparse(base_url)
        self._host = u.hostname or "127.0.0.1"
        self._port = u.port
        self._ctx = ctx

    def _token(self) -> str:
        token = self._ctx.token_by_port.get(self._port)
        if token is None:
            raise ControlError(
                f"no known control token for port {self._port}; "
                "cannot authenticate to this generation")
        return token

    def _request(self, req: dict, *, timeout: float = 5.0) -> dict:
        return control_request(self._host, self._port, self._token(), req, timeout=timeout)

    def health(self) -> dict[str, Any]:
        return self._request({"op": "ping"})

    def drain(self, *, timeout: float, poll: float, force: bool) -> dict[str, Any]:
        """Poll the ``drain`` control op until 0 attached sessions or timeout.

        Matches ``zdd.cutover``'s expected result shape. Only an observed
        zero counts as drained; the protocol's ``force`` argument cannot
        authorize retirement of attached sessions.
        """
        deadline = time.monotonic() + timeout
        busy_sessions: int | None = None
        while True:
            try:
                resp = self._request({"op": "drain"})
            except ControlError as exc:
                return {"drained": False, "clean": False, "forced": False,
                       "busy_sessions": busy_sessions, "error": str(exc)}
            if not resp.get("ok"):
                # An application-level failure (unauthorized, malformed) is
                # *not* the same thing as "0 attached sessions" -- a falsy/
                # missing busy_sessions here must never be read as "cleanly
                # drained". Surface it plainly instead of proceeding as if
                # the old daemon actually drained.
                return {"drained": False, "clean": False, "forced": False,
                       "busy_sessions": busy_sessions,
                       "error": resp.get("error") or "drain request failed"}
            busy_sessions = resp.get("busy_sessions")
            if busy_sessions is None:
                # ok:true but the field is missing entirely -- a malformed
                # reply, not a real "0 busy" observation. Treating it as
                # drained would let a genuinely-busy old daemon get shut
                # down anyway; surface it as a failure instead.
                return {"drained": False, "clean": False, "forced": False,
                       "busy_sessions": None,
                       "error": "drain reply missing busy_sessions"}
            if busy_sessions == 0:
                return {"drained": True, "clean": True, "forced": False,
                       "busy_sessions": 0}
            if time.monotonic() >= deadline:
                break
            time.sleep(poll)
        return {"drained": False, "clean": False, "forced": False,
               "busy_sessions": busy_sessions}


    def undrain(self) -> dict[str, Any]:
        """Best-effort rollback: release the old daemon's drain gate.

        ``zdd.cutover._undrain()`` (the only caller) already wraps this in a
        try/except that treats ANY raised exception as "undrain failed,
        non-fatal, but record it" -- it never inspects a returned dict at
        all. A rejected reply ({"ok": false, ...} -- unauthorized/malformed)
        must therefore raise, not just return, or the old daemon could be
        left stuck in draining mode after a rollback with no trace of why.
        """
        resp = self._request({"op": "undrain"})
        if not resp.get("ok"):
            raise ControlError(
                f"undrain request rejected by old daemon: "
                f"{resp.get('error') or resp!r}")
        return resp

    def shutdown(self) -> dict[str, Any]:
        """The cutover's actual commit point (``zdd.cutover`` calls this
        exactly once, on the *old* client, right after its final pre-retire
        health re-check passes and before returning). The fixed, client-
        facing data handle MUST be flipped to the new generation here --
        before sending the shutdown op, not after ``CutoverOrchestrator.run()``
        returns in ``run_cutover()`` -- or there is a real window where the
        old daemon has already been told to stop (and may unlink/stop
        serving its socket) while the fixed handle still points at it,
        undermining the whole point of a zero-downtime cutover."""
        from . import ipc
        from . import serve as _serve

        _serve.flip_data_handle(
            self._ctx.new_socket_path, legacy_path=ipc.default_socket_path())
        resp = self._request({"op": "shutdown"})
        if not resp.get("ok"):
            # zdd.cutover treats a returned dict (any dict) from shutdown()
            # as success -- it never inspects the reply's contents. A
            # rejected shutdown (unauthorized/malformed) must not be
            # silently read as "old daemon retired": the fixed handle was
            # ALREADY flipped above (this is the true commit point), so
            # zdd will report a committed cutover even though the old
            # generation is still running. Raise instead, so the
            # orchestrator records this as a post-commit error rather than
            # a clean success.
            raise ControlError(
                f"shutdown request rejected by old daemon: "
                f"{resp.get('error') or resp!r}")
        return resp

    def adopt_relay(self) -> dict[str, Any]:
        # agent-mcp has no credential relay to hand off; no-op, matching the
        # zdd _Client protocol's optional-for-non-relay-consumers shape.
        return {}


def _fixed_handle_has_live_listener(data_root: Path, *, fixed: Path | None = None) -> bool:
    """Probe the fixed data socket path itself for an actual live listener.

    A genuinely pre-cutover-feature daemon never calls ``routing.
    publish_active()`` at all (that whole subsystem didn't exist yet), so it
    leaves NO routing-table entry -- meaning ``read_active_endpoint()``
    returns ``None`` for it, identically to true "nothing running here."
    ``run_cutover()`` must not conflate the two: a live pre-feature daemon is
    still holding the single-instance lease and the fixed socket, and
    blindly proceeding down the cold-start path would spawn a *second*
    daemon and flip the fixed handle to point at it while the old one keeps
    running -- exactly the bootstrap-boundary hazard this check exists to
    prevent. A real OS-level connect (no bytes sent) distinguishes "a live
    process is listening" from "an artifact (stale socket file/sidecar) is
    present but dead" -- the latter is the normal, safe cold-start case.
    """
    from . import sockio

    path = fixed or sockio.default_socket_path()
    try:
        sock, _token = sockio._connect(path)
    except sockio.HostUnreachableError:
        return False
    sockio._close(sock)
    return True


def source_fixed_socket() -> Path:
    """Resolve the data handle independently of the installation selector."""
    from . import ipc, sockio

    return Path(sockio.absolute_handle(
        ipc.default_socket_path(),
    ))


def source_handle_state(fixed: Path) -> tuple:
    """Retain actual data-handle custody without a second identity protocol."""
    from . import serve

    for parent in (fixed.parent, *fixed.parent.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ControlError("MCP data home must have ordinary directory ancestors")
    for name in ("zdd-cutover.lock", "agent-mcp-serve.lock", "active.lock", "active.json"):
        try:
            info = (fixed.parent / name).lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise ControlError("MCP control custody must use ordinary unshared files")
    path = fixed if serve._HAS_AF_UNIX else serve._endpoint_path(fixed)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return ("absent",)
    if serve._HAS_AF_UNIX:
        if stat.S_ISLNK(info.st_mode):
            content = os.readlink(path)
            target = path.resolve(strict=False)
            if target.parent != fixed.parent:
                raise ControlError("MCP data handle has a foreign socket target")
            try:
                target_info = target.stat()
            except FileNotFoundError:
                target_info = None
            if target_info is not None and not stat.S_ISSOCK(target_info.st_mode):
                raise ControlError("MCP data handle has a foreign socket target")
            if os.readlink(path) != content:
                raise ControlError("MCP data handle changed during admission")
        elif stat.S_ISSOCK(info.st_mode):
            content = None
        else:
            raise ControlError("MCP data handle is not an owned socket")
    else:
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_size > 65536
        ):
            raise ControlError("MCP data endpoint must be a bounded ordinary file")
        content = path.read_bytes()
        if serve._read_endpoint(fixed) is None:
            raise ControlError("MCP data endpoint is invalid")
    after = path.lstat()
    if (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size) != (
        after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size,
    ):
        raise ControlError("MCP data handle changed during admission")
    return ("present", info.st_dev, info.st_ino, info.st_mtime_ns, content)


def source_serving_intent() -> bool:
    """Observe optional serving before selection changes; this is not permission."""
    from zdd import routing
    from . import sockio

    fixed = source_fixed_socket()
    return bool(
        routing.read_active_endpoint(fixed.parent)
        or _fixed_handle_has_live_listener(fixed.parent, fixed=fixed)
        or sockio.legacy_serving_hint(fixed)
    )


def source_data_owner(fixed: Path) -> int | None:
    """Identify the native data listener without requiring the old control protocol."""
    from . import serve

    if serve._HAS_AF_UNIX:
        darwin = sys.platform == "darwin"
        if not darwin and not hasattr(_socket, "SO_PEERCRED"):
            raise ControlError("Native Unix data-listener identity is unavailable")
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as peer:
            peer.settimeout(0.25)
            try:
                peer.connect(str(fixed))
            except (FileNotFoundError, ConnectionRefusedError):
                return None
            try:
                if darwin:
                    # Darwin sys/un.h: SOL_LOCAL=0, LOCAL_PEERPID=2. Unlike
                    # getpeereid(), this returns the connected peer's native PID.
                    identity = peer.getsockopt(0, 2, struct.calcsize("i"))
                    pid = struct.unpack("i", identity)[0]
                else:
                    identity = peer.getsockopt(
                        _socket.SOL_SOCKET, _socket.SO_PEERCRED, struct.calcsize("3i"),
                    )
                    pid = struct.unpack("3i", identity)[0]
            except (OSError, struct.error) as error:
                raise ControlError("Native Unix data-listener identity is unavailable") from error
            if pid <= 0:
                raise ControlError("Native Unix data-listener identity is unavailable")
            return pid
    endpoint = serve._read_endpoint(fixed)
    if endpoint is None:
        return None
    if sys.platform != "win32":
        raise ControlError("Native TCP data-listener identity is unavailable")
    import ctypes
    from ctypes import wintypes

    query = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    query.argtypes = (
        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
        wintypes.ULONG, ctypes.c_int, wintypes.ULONG,
    )
    query.restype = wintypes.DWORD
    size = wintypes.DWORD()
    if query(None, ctypes.byref(size), False, _socket.AF_INET, 3, 0) not in (0, 122):
        raise ControlError("Native TCP listener inspection failed")
    for _ in range(3):
        if not 4 <= size.value <= 4 << 20:
            raise ControlError("Native TCP listener table exceeds its inspection bound")
        buffer = ctypes.create_string_buffer(size.value)
        result = query(buffer, ctypes.byref(size), False, _socket.AF_INET, 3, 0)
        if result == 122:
            continue
        if result != 0:
            raise ControlError("Native TCP listener inspection failed")
        content = buffer.raw
        count = struct.unpack_from("<I", content)[0]
        if count > (len(content) - 4) // 24:
            raise ControlError("Native TCP listener table is incomplete")
        owners = set()
        for index in range(count):
            state, address, port, _remote, _remote_port, pid = struct.unpack_from(
                "<6I", content, 4 + index * 24,
            )
            if (
                state == 2 and _socket.ntohs(port & 0xffff) == endpoint["port"]
                and _socket.inet_ntoa(struct.pack("<I", address)) in {"127.0.0.1", "0.0.0.0"}
            ):
                owners.add(pid)
        if len(owners) > 1:
            raise ControlError("Native TCP data-listener ownership is ambiguous")
        return next(iter(owners), None)
    raise ControlError("Native TCP listener table changed during inspection")


def _require_selected(layout: tuple) -> None:
    path = layout[2] / "current-version"
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_size > 4096
        ):
            raise ControlError("MCP selected generation must use a bounded ordinary file")
        selected = path.read_text(encoding="utf-8-sig").strip()
        after = path.lstat()
        if (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size) != (
            after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size,
        ):
            raise ControlError("MCP selected generation changed during admission")
    except ControlError:
        raise
    except (OSError, UnicodeError) as error:
        raise ControlError("MCP selected generation is unavailable") from error
    if selected != layout[0]:
        raise ControlError("MCP callback slot is not the selected generation")


def _installed_slot(*, require_selected: bool = True) -> tuple:
    from importlib.metadata import PackageNotFoundError, version

    from agent_procutil._daemon_instance_identity import runtime_slot_paths

    from .source_build import effective_runtime_version, is_source_build_version

    prefix = Path(sys.prefix)
    layout = runtime_slot_paths(prefix)
    if layout is None or not Path(__file__).resolve().is_relative_to(prefix.resolve()):
        raise ControlError("MCP callback is not running from its installed slot environment")
    declared_root = os.environ.get("AGENT_MCP_INSTALL_DIR")
    if declared_root and Path(declared_root).resolve() != Path(layout[2]).resolve():
        raise ControlError("MCP callback installation root differs from its physical slot")
    for parent in (prefix, *prefix.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ControlError("MCP callback slot must have ordinary directory ancestors")
    try:
        package = version("agent-mcp")
    except PackageNotFoundError as error:
        raise ControlError("MCP callback package metadata is unavailable") from error
    physical = effective_runtime_version(package, prefix=prefix, module_path=Path(__file__))
    if (
        physical != layout[0]
        and package.lower().replace("-", ".").replace("_", ".")
        != layout[0].lower().replace("-", ".").replace("_", ".")
    ):
        raise ControlError("MCP callback package and physical generation differ")
    if prefix.name != ".venv" and not is_source_build_version(layout[0]):
        raise ControlError("Released MCP callbacks require the installed UV slot environment")
    if require_selected:
        _require_selected(layout)
    return layout


def _configured_readiness() -> dict:
    from .installer_readiness import configured_readiness

    return configured_readiness()


def _intent_path(layout: tuple) -> Path:
    return Path(layout[1]) / ".mcp-serving-intent.json"


def _read_intent(path: Path, generation: str) -> dict:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_size > 4096
    ):
        raise ControlError("MCP serving intent is not a bounded ordinary slot file")
    with path.open("rb") as stream:
        content = stream.read(4097)
    if len(content) > 4096:
        raise ControlError("MCP serving intent exceeds its slot-file bound")
    record = json.loads(content)
    if (
        not isinstance(record, dict) or set(record) != {"generation", "wanted"}
        or record["generation"] != generation or type(record["wanted"]) is not bool
    ):
        raise ControlError("MCP serving intent differs from its installed slot")
    return record


def source_setup() -> dict:
    """Prepare a physical candidate's serving intent before selector publication."""
    layout = _installed_slot(require_selected=False)
    readiness = _configured_readiness()
    if readiness["state"] == "failed":
        raise ControlError(readiness["detail"])
    wanted = readiness["state"] == "ready" and source_serving_intent()
    path = _intent_path(layout)
    if path.exists() or path.is_symlink():
        _read_intent(path, layout[0])
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    created = False
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            created = True
            json.dump({"generation": layout[0], "wanted": wanted}, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() or path.is_symlink():
            _read_intent(path, layout[0])
        os.replace(temporary, path)
    finally:
        if created:
            temporary.unlink(missing_ok=True)
    return {"ok": True, "state": readiness["state"], "wanted": wanted}


def source_start() -> dict:
    """Apply only the selected installed slot's preserved optional serving intent."""
    layout = _installed_slot()
    if os.environ.get("AGENT_MCP_NO_SERVE") or os.environ.get("AGENT_MCP_NO_CUTOVER"):
        return {"ok": True, "skipped": "serving disabled"}
    intent = _read_intent(_intent_path(layout), layout[0])
    readiness = _configured_readiness()
    if readiness["state"] == "failed":
        raise ControlError(readiness["detail"])
    if not intent["wanted"] or readiness["state"] != "ready":
        return {"ok": True, "skipped": "serving not configured/wanted"}
    _require_selected(layout)
    return _run_slot_startup(
        layout, layout[0],
        health_timeout=float(os.environ.get("AGENT_MCP_CUTOVER_HEALTH_TIMEOUT", "15")),
    )


def run_source_startup(*, health_timeout: float = 60.0) -> dict:
    """Start selected Source code without controlling its predecessor."""
    from .source_build import is_source_build_version

    layout = _installed_slot()
    if not is_source_build_version(layout[0]):
        raise ControlError("Source startup requires the exact selected physical generation")
    return _run_slot_startup(layout, layout[0], health_timeout=health_timeout)


def _run_slot_startup(
    layout: tuple, physical_version: str, *, health_timeout: float,
    fixed_socket: str | Path | None = None,
) -> dict:
    from zdd import CutoverLock, routing
    from zdd.diagnostics import process_start_time

    from . import ipc, serve, sockio, source_serving

    if not 0 < health_timeout <= 3600:
        raise ControlError("Source startup requires a bounded positive health timeout")
    _require_selected(layout)
    fixed = Path(sockio.absolute_handle(fixed_socket)) if fixed_socket is not None else source_fixed_socket()
    home = fixed.parent
    home.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + health_timeout
    source_handle_state(fixed)
    with CutoverLock(home):
        handle = source_handle_state(fixed)
        table = routing.read_table(home, strict=True) or {}
        raw = table.get("active")
        previous = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None

        def refuse_existing_owner(current_raw, *, qualify_installation=True):
            current = (
                routing.Endpoint.from_dict(current_raw) if isinstance(current_raw, dict)
                else None
            )
            if current_raw is not None and (
                current is None or not current.pid or not current.process_start_time
                or current.client_host not in {"127.0.0.1", "::1"}
                or (routing._pid_alive(current.pid)
                    and process_start_time(current.pid) != current.process_start_time)
            ):
                return "MCP current native route ownership is unavailable or foreign"
            owner = source_data_owner(fixed)
            if handle[0] != "absent" and current is None and owner is not None:
                return "MCP data handle has no attributable serving owner"
            if owner is not None and (
                current is None or owner != current.pid
                or process_start_time(owner) != current.process_start_time
            ):
                return "MCP data listener has an unknown or foreign native owner"
            if qualify_installation and current is not None and routing._pid_alive(current.pid):
                source_serving.require_installation_owner(fixed, current, Path(layout[2]))
            return None

        refusal = refuse_existing_owner(raw, qualify_installation=False)
        if refusal:
            raise routing.ActivePublicationRefused(refusal)
        reusable = source_serving.ready_owner(
            fixed, previous, physical_version, runtime_root=Path(layout[2]), deadline=deadline,
        )
        _require_selected(layout)
        if reusable is not None:
            return reusable
        generation = max(
            (endpoint.generation for role in ("active", "previous")
             if (endpoint := routing.Endpoint.from_dict(table.get(role, {}))) is not None),
            default=0,
        ) + 1
        retained_socket = (
            (home / handle[4]).resolve(strict=False)
            if serve._HAS_AF_UNIX and handle[0] == "present" and handle[4] is not None
            else None
        )
        while True:
            if time.monotonic() >= deadline:
                raise ControlError("Source startup exhausted its candidate endpoint deadline")
            candidate_socket = home / f"serve-g{generation}.sock"
            occupied = candidate_socket == retained_socket
            for path in (candidate_socket, serve._endpoint_path(candidate_socket)):
                try:
                    path.lstat()
                except FileNotFoundError:
                    continue
                occupied = True
                break
            if not occupied:
                break
            generation += 1
        ctx = _CutoverContext(
            home, candidate_socket, home / f"serve-g{generation}.log",
            secrets.token_hex(16), None, {}, config_home=ipc.default_home_dir(), fixed_socket=fixed,
        )
        port = pick_free_port()
        process = spawn_passive_daemon(ctx, port)
        publication_attempted = False
        try:
            birth = process_start_time(process.pid)
            if not birth:
                raise ControlError("Source MCP incoming native identity is unavailable")
            while True:
                try:
                    reply = control_request(
                        "127.0.0.1", port, ctx.new_control_token, {"op": "ping"}, timeout=0.25,
                    )
                except ControlError:
                    reply = {}
                if (
                    reply.get("ok") is True and reply.get("pong") is True
                    and reply.get("ready") is True
                    and not reply.get("draining") and reply.get("pid") == process.pid
                    and reply.get("version") == physical_version
                    and reply.get("data_socket") == str(ctx.new_socket_path)
                    and source_serving.installation_matches(reply, Path(layout[2]))
                ):
                    break
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise ControlError("Source MCP incoming server failed readiness")
                time.sleep(0.05)
            if process_start_time(process.pid) != birth:
                raise ControlError("Source MCP incoming process identity changed")
            incoming = routing.Endpoint(
                bind="127.0.0.1", port=port, pid=process.pid, version=physical_version,
                generation=generation, process_start_time=birth,
            )

            def refuse_current(current_raw):
                _require_selected(layout)
                if source_handle_state(fixed) != handle:
                    return "MCP data handle changed before source publication"
                if process.poll() is not None or process_start_time(process.pid) != birth:
                    return "Source MCP incoming native identity changed"
                return refuse_existing_owner(current_raw)

            publication_attempted = True
            routing.publish_active_with_previous_guarded(
                home, bind="127.0.0.1", port=port, pid=process.pid, version=physical_version,
                generation=generation, demote_existing=True, expected_active=previous,
                refuse_current=refuse_current, process_start_time=birth, lock_deadline=deadline,
            )
            serve.flip_data_handle(ctx.new_socket_path, legacy_path=fixed, expected_state=handle)
            return {
                "ok": True, "pid": process.pid, "version": physical_version,
                "data_socket": str(ctx.new_socket_path), "runtime_root": str(layout[2]),
            }
        except BaseException as error:
            if publication_attempted:
                recovered = source_serving.recover_publication(
                    layout, fixed, ctx.new_socket_path, handle, previous, incoming, process,
                    deadline=deadline,
                )
                if recovered is not None:
                    if isinstance(error, Exception):
                        return recovered
                    raise
            try:
                control_request(
                    "127.0.0.1", port, ctx.new_control_token, {"op": "shutdown"}, timeout=0.5,
                )
                process.wait(timeout=2)
            except (ControlError, subprocess.TimeoutExpired):
                process.terminate()
                process.wait(timeout=5)
            raise


def _protect_drain_diagnostic(result: Any) -> None:
    if (
        not result.ok and result.error and result.drain and result.drain.get("busy_sessions")
        and not result.drain.get("drained") and not result.drain.get("error")
    ):
        result.error = result.error.replace(
            "; rerun with force to proceed",
            "; attached sessions cannot be forcibly retired",
        )


def run_cutover(*, health_timeout: float = 60.0, drain_timeout: float = 300.0,
               force: bool = False, require_live_daemon: bool = False) -> dict:
    """Drive one cutover of the resident ``serve`` daemon. Returns a result
    dict; the CLI (`_cmd_cutover` in `__main__.py`) owns how it's printed.

    ``require_live_daemon=True`` is the installer-safe mode (Phase 2,
    ``agent-mcp-graceful-cutover``): `serve` is optional, on-demand warmth,
    never a registered service, so an installer/reconcile pass invoking
    `cutover` unconditionally on every activation must NOT be the thing that
    starts a resident daemon where none was running. With this set, a call
    that would otherwise take the "cold start" path (no live daemon at all)
    or would cut over a daemon that is already running this exact version
    instead returns ``{"ok": True, "skipped": "<reason>"}`` without spawning
    anything. A live daemon on a genuinely different (or pre-feature) version
    still cuts over / reports its usual error exactly as before -- this flag
    only suppresses the *unconditional* spawn-from-nothing and no-op-repeat
    cases, never the bootstrap-boundary or mid-version-mismatch fixes."""
    from zdd import routing
    from zdd.cutover import CutoverOrchestrator

    from . import __version__, ipc
    from . import serve as _serve

    class _McpCutoverOrchestrator(CutoverOrchestrator):
        def _rollback(
            self, old: Any, handle: Any, new_port: int, result: Any, **kwargs: Any,
        ) -> None:
            _protect_drain_diagnostic(result)
            super()._rollback(old, handle, new_port, result, **kwargs)

    data_root = ipc.default_socket_path().parent
    data_root.mkdir(parents=True, exist_ok=True)

    # verify_listener=True (the safe default) is deliberate here: a stale
    # active.json entry left by a crashed daemon (no live listener, pid
    # confirmed dead) must self-heal to "no active daemon" -- the same as an
    # empty table -- so a cold-start cutover proceeds normally instead of
    # incorrectly reading the dead entry as "current" and then hitting the
    # bootstrap-boundary error meant for a genuinely *live* pre-feature
    # daemon (whose token file just happens to be missing/gone).
    current = routing.read_active_endpoint(data_root)
    next_gen = (current.generation + 1) if current else 1

    if require_live_daemon:
        live = current is not None or _fixed_handle_has_live_listener(data_root)
        if not live:
            return {"ok": True, "skipped": "no live daemon to cut over"}
        if current is not None and current.version == __version__:
            return {"ok": True, "skipped": "already on this version"}

    if current is None and _fixed_handle_has_live_listener(data_root):
        # A genuinely pre-cutover-feature daemon never publishes to the
        # routing table (that subsystem didn't exist yet), so it looks
        # identical to "nothing running" by routing-table state alone. But
        # it IS actually listening on the fixed socket and holding the
        # single-instance lease -- proceeding down the cold-start path
        # would spawn a second daemon and flip the fixed handle to it while
        # this one keeps running. Refuse with the same bootstrap-boundary
        # error as the "routing entry present but no token" case below.
        return {
            "ok": False,
            "error": "a serve daemon is already listening on the fixed "
                     "socket but has no routing-table entry (it predates "
                     "the cutover feature) -- this is a one-time bootstrap "
                     "boundary; stop it and let the next call/materialize/"
                     "attach respawn the new version normally, or restart "
                     "it manually",
        }

    # Resolve the OLD daemon's control token *now*, keyed on its own pid --
    # never a single shared filename, so a stale token left behind by an
    # unrelated (e.g. previously rolled-back) generation can never be
    # mistaken for the real old daemon's.
    old_token = None
    if current is not None and current.pid is not None:
        try:
            old_token = _serve._control_token_path(
                data_root, current.pid).read_text(encoding="utf-8").strip()
        except OSError:
            old_token = None
        if not old_token:
            # An empty/whitespace-only sidecar (a torn write, a truncated
            # file) is not a usable token -- treat it exactly like "missing"
            # so it correctly falls into the bootstrap-boundary check below,
            # instead of silently attempting an unauthorized drain/shutdown
            # later that's much harder to diagnose.
            old_token = None

    ctx = _CutoverContext(
        data_root=data_root,
        new_socket_path=data_root / f"serve-g{next_gen}.sock",
        new_log_path=data_root / f"serve-g{next_gen}.log",
        new_control_token=secrets.token_hex(16),
        old_control_token=old_token,
        token_by_port={},
        config_home=ipc.default_home_dir(),
        fixed_socket=ipc.default_socket_path(),
    )
    if current is not None and old_token is not None:
        ctx.token_by_port[current.port] = old_token

    if current is not None and old_token is None:
        # The active daemon predates this feature (no control channel to
        # dial) -- there is nothing to cut over *to* gracefully. Report this
        # plainly rather than attempting a cutover that can only fail.
        return {
            "ok": False,
            "error": "the currently active serve daemon has no control "
                     "channel (it predates the cutover feature) -- this is "
                     "a one-time bootstrap boundary; stop it and let the "
                     "next call/materialize/attach respawn the new version "
                     "normally, or restart it manually",
        }

    orch = _McpCutoverOrchestrator(
        data_root, bind="127.0.0.1", version=__version__,
        spawn_passive=lambda port: spawn_passive_daemon(ctx, port),
        health_check=lambda host, port: health_check(ctx, host, port),
        make_client=lambda base_url: CutoverClient(base_url, ctx),
        pick_free_port=pick_free_port,
    )
    try:
        res = orch.run(health_timeout=health_timeout, drain_timeout=drain_timeout,
                       force=force)
    except ControlError as exc:
        # CutoverClient.shutdown() raises this when the old daemon rejects
        # the shutdown request (unauthorized/malformed reply). By this point
        # the fixed handle has ALREADY been flipped to the new generation
        # (shutdown() does that before sending the op -- the true commit
        # point) and zdd has already set result.committed = True internally,
        # so the new generation genuinely IS the live daemon now. What did
        # NOT happen is confirmed retirement of the old one -- report that
        # plainly instead of letting zdd's caller-ignores-the-return-value
        # design silently read this as a clean success.
        return {
            "ok": False, "committed": True, "rolled_back": False,
            "new_port": None, "old_port": None,
            "data_socket": str(ctx.new_socket_path),
            "error": f"cutover committed to the new generation, but the old "
                     f"daemon's shutdown request was rejected -- it may "
                     f"still be running and must be checked/stopped "
                     f"manually: {exc}",
            "steps": ["routing table flipped -> new active",
                     f"old daemon shutdown REJECTED: {exc}"],
            "drain": None,
        }
    _protect_drain_diagnostic(res)
    result = res.to_dict()
    if res.ok:
        result["data_socket"] = str(ctx.new_socket_path)
        if current is None:
            # Cold start: zdd's cutover never calls old_client.shutdown() when
            # there was no prior active daemon to retire (nothing to drain),
            # so CutoverClient.shutdown()'s flip -- the normal trigger -- never
            # ran. Flip explicitly here; there is no race to close in this
            # branch (no old daemon was ever serving the fixed handle).
            _serve.flip_data_handle(
                ctx.new_socket_path, legacy_path=ipc.default_socket_path())
    return result
