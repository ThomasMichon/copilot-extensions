"""Physical-generation selection and owner-authenticated MCP self-retirement."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path


def installed_generation(version: str | None) -> tuple[Path | None, str | None]:
    """Resolve the installed selector root without confusing it with MCP data."""
    from agent_procutil._daemon_instance_identity import runtime_slot_paths

    from .source_build import is_source_build_version

    layout = runtime_slot_paths(Path(sys.prefix))
    if layout is not None and layout[0] == version and is_source_build_version(version):
        return Path(layout[2]), version
    if layout is not None and Path(sys.prefix).name == ".venv":
        from .cutover import ControlError, _installed_slot

        try:
            installed = _installed_slot(require_selected=False)
        except ControlError:
            pass
        else:
            return Path(installed[2]), installed[0]
    return None, version


def generation_selected(server) -> bool:
    """Refuse an unselected physical generation before resident startup."""
    if server._runtime_root is None:
        return True
    try:
        selected = (server._runtime_root / "current-version").read_text(
            encoding="utf-8-sig",
        ).strip()
    except (OSError, UnicodeError):
        return False
    return selected == server._version


@contextlib.contextmanager
def startup_transaction(server):
    """Serialize fixed-handle startup with the existing application cutover owner."""
    if server._runtime_root is None or server._passive:
        yield generation_selected(server)
        return
    from zdd import CutoverLock, CutoverLockedError

    with contextlib.ExitStack() as transaction:
        try:
            transaction.enter_context(CutoverLock(server.socket_path.parent))
        except CutoverLockedError:
            yield False
        else:
            yield generation_selected(server)


def data_handle_matches(fixed: Path, data_socket: Path) -> bool:
    """Observe the committed fixed handle without changing its owner."""
    from . import serve

    if data_socket.parent != fixed.parent:
        return False
    try:
        if serve._HAS_AF_UNIX:
            if fixed == data_socket:
                return fixed.is_socket()
            return fixed.is_symlink() and fixed.resolve(strict=True) == data_socket.resolve(
                strict=True,
            )
        incoming = serve._read_endpoint(data_socket)
        return bool(
            incoming and isinstance(incoming.get("token"), str) and incoming["token"]
            and serve._read_endpoint(fixed) == incoming
        )
    except (OSError, RuntimeError):
        return False


def ready_owner(fixed: Path, endpoint, version: str, *, deadline: float) -> dict | None:
    """Qualify the exact active native generation and its authenticated data handle."""
    from zdd import routing
    from zdd.diagnostics import process_start_time

    from . import serve
    from .cutover import control_request, source_data_owner

    if (
        endpoint is None or endpoint.version != version or not endpoint.pid
        or not endpoint.process_start_time or endpoint.client_host not in {"127.0.0.1", "::1"}
        or process_start_time(endpoint.pid) != endpoint.process_start_time
    ):
        return None
    try:
        token = serve._control_token_path(fixed.parent, endpoint.pid).read_text(
            encoding="utf-8",
        ).strip()
        reply = control_request(
            endpoint.client_host, endpoint.port, token, {"op": "ping"}, timeout=0.25,
        )
        if not (
            reply.get("ok") is True and reply.get("pong") is True
            and reply.get("ready") is True and not reply.get("draining")
            and reply.get("pid") == endpoint.pid and reply.get("version") == version
        ):
            return None
        data_socket = Path(reply["data_socket"])

        def refuse_current(_raw):
            if (
                process_start_time(endpoint.pid) != endpoint.process_start_time
                or not data_handle_matches(fixed, data_socket)
                or source_data_owner(fixed) != endpoint.pid
            ):
                return "MCP ready data owner changed during qualification"
            return None

        routing.validate_active(
            fixed.parent, endpoint, refuse_current=refuse_current, lock_deadline=deadline,
        )
        return {
            "ok": True, "pid": endpoint.pid, "version": version,
            "data_socket": str(data_socket),
        }
    except (OSError, ValueError, KeyError, TypeError, routing.ActivePublicationRefused):
        return None


def recover_publication(
    layout: tuple, fixed: Path, data_socket: Path, handle: tuple, previous, incoming,
    process, *, deadline: float,
) -> dict | None:
    """Restore only an uncommitted owned route; retain ambiguous or committed owners."""
    from zdd import routing
    from zdd.diagnostics import process_start_time

    from .cutover import ControlError, _require_selected, source_data_owner, source_handle_state

    current_table = routing.read_table(fixed.parent, strict=True) or {}
    current = routing.Endpoint.from_dict(current_table.get("active", {}))
    if data_handle_matches(fixed, data_socket):
        if current == incoming:
            result = ready_owner(fixed, incoming, incoming.version, deadline=deadline)
            _require_selected(layout)
            if result is not None and Path(result["data_socket"]) == data_socket:
                return result
        raise ControlError("MCP data publication committed; exact incoming readiness is unconfirmed")
    if source_handle_state(fixed) != handle:
        raise ControlError("MCP data publication is ambiguous; candidate and newer ownership retained")
    if current != incoming:
        if current == previous:
            return None
        raise ControlError("MCP active ownership changed; candidate and newer ownership retained")

    def refuse_restore(_raw):
        _require_selected(layout)
        if source_handle_state(fixed) != handle:
            return "MCP data handle changed before publication recovery"
        if process.poll() is None and process_start_time(process.pid) != incoming.process_start_time:
            return "MCP incoming native identity changed before publication recovery"
        owner = source_data_owner(fixed)
        if owner is not None and (
            previous is None or owner != previous.pid
            or process_start_time(owner) != previous.process_start_time
        ):
            return "MCP previous data listener has unknown or foreign ownership"
        return None

    if previous is None:
        routing.withdraw_active_guarded(
            fixed.parent, expected_active=incoming, refuse_current=refuse_restore,
            lock_deadline=deadline,
        )
    else:
        routing.publish_active_with_previous_guarded(
            fixed.parent, bind=previous.bind, port=previous.port, pid=previous.pid,
            version=previous.version, process_start_time=previous.process_start_time,
            expected_active=incoming, refuse_current=refuse_restore, lock_deadline=deadline,
        )
    return None


def ready_replacement(server) -> bool:
    """Observe the selected ready native owner and its committed data handle."""
    if server._runtime_root is None:
        return False
    from zdd import routing
    from zdd.diagnostics import process_start_time

    from . import serve
    from .cutover import ControlError, control_request, source_fixed_socket

    try:
        selected = (server._runtime_root / "current-version").read_text(
            encoding="utf-8-sig",
        ).strip()
        table = routing.read_table(server._config_dir(), strict=True)
        endpoint = routing.Endpoint.from_dict(table.get("active", {})) if table else None
        if (
            endpoint is None or not endpoint.pid or endpoint.pid == os.getpid()
            or endpoint.version != selected or not endpoint.process_start_time
            or process_start_time(endpoint.pid) != endpoint.process_start_time
            or endpoint.client_host not in {"127.0.0.1", "::1"}
        ):
            return False
        token = serve._control_token_path(server._config_dir(), endpoint.pid).read_text(
            encoding="utf-8",
        ).strip()
        reply = control_request(
            endpoint.client_host, endpoint.port, token, {"op": "ping"}, timeout=0.25,
        )
        if not (
            reply.get("ok") is True and reply.get("pong") is True
            and reply.get("ready") is True
            and not reply.get("draining") and reply.get("pid") == endpoint.pid
            and reply.get("version") == selected
        ):
            return False
        data_socket = Path(reply["data_socket"])
        if data_socket.parent != server.socket_path.parent:
            return False
        fixed = source_fixed_socket()
        if serve._HAS_AF_UNIX:
            data_ready = fixed.is_symlink() and fixed.resolve() == data_socket.resolve()
        else:
            incoming = serve._read_endpoint(data_socket)
            data_ready = incoming is not None and serve._read_endpoint(fixed) == incoming
        current = routing.read_table(server._config_dir(), strict=True)
        return bool(
            data_ready and current
            and routing.Endpoint.from_dict(current.get("active", {})) == endpoint
            and process_start_time(endpoint.pid) == endpoint.process_start_time
            and (server._runtime_root / "current-version").read_text(
                encoding="utf-8-sig",
            ).strip() == selected
        )
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, ControlError):
        return False


async def inspect_replacement(server) -> bool:
    """Keep native inspection inside resident custody through cancellation."""
    if server._runtime_root is None:
        return False
    from .serve import _join_cleanup

    readiness = asyncio.create_task(asyncio.to_thread(server._ready_replacement))
    try:
        return await asyncio.shield(readiness)
    except asyncio.CancelledError:
        await _join_cleanup(readiness)
        raise
