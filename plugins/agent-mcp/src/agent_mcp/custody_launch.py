"""Qualified shared-custody startup; never reclaim the central backend handle."""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

from . import incoming, sockio
from .custody_conduit import _exchange, attach as attach_conduit
from .custody_contract import supports
from .execution_context import SharingUnavailable


def _qualify_backend(handle, admission):
    from .cutover import _require_selected
    from .source_serving import installation_matches

    root = Path(admission.root)
    if not root.is_absolute() or not root.is_dir():
        raise SharingUnavailable("custody requires an admitted installation")
    connection, token = sockio._connect(handle, timeout=2)
    try:
        if connection.family in (socket.AF_INET, socket.AF_INET6) and (
            not isinstance(token, str) or not token
        ):
            raise SharingUnavailable("backend endpoint authentication is missing")
        reply = _exchange(connection, token, {"op": "ping"}, time.monotonic() + 2)
    finally:
        sockio._close(connection)
    if (
        reply.get("ok") is not True or reply.get("pong") is not True
        or reply.get("version") != admission.generation or not installation_matches(reply, root)
        or not incoming.supports(reply.get("fresh_admission"))
        or not supports(reply.get("custody_backend"))
        or reply.get("ready") is not True or reply.get("draining") is not False
    ):
        raise SharingUnavailable("current backend does not support session custody")
    _require_selected((admission.generation, None, root))
    return root.resolve()


def _spawn(keeper, backend, root):
    from agent_procutil import spawn_daemon, windowless_python, windowless_python_env
    from agent_procutil.daemon_instances import DaemonInstanceSpec

    home = sockio.default_home_dir().resolve()
    if os.getcwd() != str(home):
        raise SharingUnavailable("custody startup requires actually compatible stable cwd")
    environment = sockio.daemon_env_overrides(home)
    environment.update(windowless_python_env(sys.executable))
    return spawn_daemon(
        [windowless_python(sys.executable), "-m", "agent_mcp.custody_host",
         "--socket", str(keeper), "--backend", str(backend), "--runtime-root", str(root)],
        cwd=home, log_path=home / "custody.log",
        instance=DaemonInstanceSpec(
            "agent-mcp-custody", keeper.parent, Path(__file__).resolve(),
        ),
        env_overrides=environment,
        contained_fallback=False, display="agent_mcp custody",
    )


def attach(handle, bridge, admission, *, ensure=True):
    """Prepare an adopted conduit without changing ordinary forwarder selection.

    Default caller selection stays unchanged. An explicit owner CLI handoff can
    invoke this path. A keeper connect failure
    must never escape as authority to delete a healthy backend's fixed handle.
    """
    if not isinstance(admission, incoming.Admission):
        raise SharingUnavailable("custody requires current backend admission")
    backend = Path(sockio.absolute_handle(handle))
    root = _qualify_backend(backend, admission)
    keeper = backend.parent / "custody-v1" / backend.name
    try:
        try:
            connection = attach_conduit(keeper, bridge, root)
        except sockio.HostUnreachableError as exc:
            if not ensure:
                raise SharingUnavailable("custody unavailable; direct execution is not reattachment") from exc
            _spawn(keeper, backend, root)
            deadline = time.monotonic() + 5
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SharingUnavailable("custody startup deadline expired") from exc
                try:
                    connection = attach_conduit(keeper, bridge, root, timeout=min(remaining, 1))
                    break
                except sockio.HostUnreachableError:
                    time.sleep(min(0.05, remaining))
        try:
            from .cutover import _require_selected

            _require_selected((admission.generation, None, root))
        except BaseException:
            sockio._close(connection)
            raise
        return connection
    except sockio.HostUnreachableError as exc:
        raise SharingUnavailable("custody unreachable; backend publication retained") from exc


def run_adopted(handle, bridge, *, owner_pid, owner_birth):
    """Dedicated opt-in process entrypoint with originating-session lifetime.

    The originating launcher supplies its own native identity before any shim
    can exit. Unsupported or unverifiable ownership refuses before custody
    startup; default forwarders do not select this entrypoint.
    """
    from .custody_lifetime import SessionOwner

    with SessionOwner(owner_pid, owner_birth) as owner:
        admission = incoming.ensure_current(handle)
        connection = attach(handle, bridge, admission)
        sockio.pump(connection, session_owner=owner)
    # Connection and native owner pin are settled before exiting. Buffered stdin
    # may remain parked on a writer retained by someone other than the dead owner.
    os._exit(0)
