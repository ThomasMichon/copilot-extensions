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

Scope note (v1): this drives the cutover state machine and the one-shot/
attach-session drain semantics. It does **not** yet include the "generation
self-retire" watchdog backstop `docs/patterns/graceful-daemon-cutover.md`
describes as an optional hardening even for its reference implementation, nor
automatic installer-triggered cutover on every version bump -- both are
explicitly named follow-up phases (see the ``agent-mcp-graceful-cutover``
effort). A daemon that predates this module has no control channel to dial;
that one-time bootstrap transition is a plain stop/start, not a regression.
"""

from __future__ import annotations

import json
import os
import secrets
import socket as _socket
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


def _passive_stdio_kwargs(log_path: Path) -> tuple[dict, list]:
    """Redirect the spawned passive daemon's stdio to its own log file.

    Without this, a POSIX child inherits whatever fds the launcher held open
    at spawn time -- a long-running daemon holding such a pipe open means the
    *launcher's* pipe/terminal never sees EOF, exactly the class of bug fixed
    in agent-bridge's ``spawn_passive`` (copilot-extensions#2200/#2201).
    """
    log = open(log_path, "ab")
    return {"stdout": log, "stderr": log, "stdin": subprocess.DEVNULL}, [log]


def spawn_passive_daemon(ctx: _CutoverContext, control_port: int) -> subprocess.Popen:
    """``zdd`` ``spawn_passive`` callback: launch the installed code as a
    passive ``serve`` instance on its own data socket + this control port.

    Uses the repo's standard headless/detached spawn helpers (the same
    pattern as ``forward._spawn_serve_host``) rather than a hand-rolled
    platform check: ``windowless_python()`` avoids a Windows venv
    ``python.exe`` re-exec flashing a fresh console, and ``detached_kwargs()``
    fully detaches the child (own session on POSIX, ``DETACHED_PROCESS |
    CREATE_NEW_PROCESS_GROUP`` on Windows) so it outlives this short-lived
    cutover process and its lifetime never drifts from the existing
    detach pattern used elsewhere in this plugin.
    """
    from agent_procutil import detached_kwargs, windowless_python

    cmd = [windowless_python(sys.executable), "-m", "agent_mcp", "serve",
          "--socket", str(ctx.new_socket_path),
          "--passive", "--control-port", str(control_port)]
    env = dict(os.environ)
    env["AGENT_MCP_CONTROL_TOKEN"] = ctx.new_control_token
    kwargs, opened = _passive_stdio_kwargs(ctx.new_log_path)
    kwargs["env"] = env
    kwargs.update(detached_kwargs())
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    finally:
        for f in opened:
            f.close()
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

        Matches ``zdd.cutover``'s exact expected shape: ``drained`` (bool --
        did it reach 0 busy, with or without being forced), ``clean`` (bool --
        reached 0 busy *without* forcing), ``forced``, ``busy_sessions`` (the
        last observed count, for its error message on failure).
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
        if force:
            return {"drained": True, "clean": False, "forced": True,
                   "busy_sessions": busy_sessions}
        return {"drained": False, "clean": False, "forced": False,
               "busy_sessions": busy_sessions}


    def undrain(self) -> dict[str, Any]:
        return self._request({"op": "undrain"})

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


def _fixed_handle_has_live_listener(data_root: Path) -> bool:
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

    path = sockio.default_socket_path()
    try:
        sock, _token = sockio._connect(path)
    except sockio.HostUnreachableError:
        return False
    sockio._close(sock)
    return True


def run_cutover(*, health_timeout: float = 60.0, drain_timeout: float = 300.0,
               force: bool = False) -> dict:
    """Drive one cutover of the resident ``serve`` daemon. Returns a result
    dict; the CLI (`_cmd_cutover` in `__main__.py`) owns how it's printed."""
    from zdd import routing
    from zdd.cutover import CutoverOrchestrator

    from . import __version__, ipc
    from . import serve as _serve

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

    orch = CutoverOrchestrator(
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
