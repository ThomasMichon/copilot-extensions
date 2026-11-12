"""Asyncio-free adoption handshake for the existing client-bound byte pump."""

from __future__ import annotations

import hmac
import json
import math
import socket
import time
from pathlib import Path

from . import execution_context, sockio
from .checkpoint import Binding
from .config import load_config
from .custody_contract import CONTRACT, supports, supports_trace_binding
from .request_context import capture_context, supports_context


def _exchange(connection, token, request, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("custody handshake deadline expired")
    connection.settimeout(remaining)
    if token is not None:
        request = {**request, "token": token}
    connection.sendall((json.dumps(request, allow_nan=False) + "\n").encode())
    line = sockio._read_ack_line(connection, deadline=deadline)
    if not line.endswith(b"\n"):
        raise OSError("custody handshake reply is incomplete")
    reply = json.loads(line)
    if not isinstance(reply, dict):
        raise OSError("custody handshake reply must be an object")
    return reply


def attach(handle, bridge, runtime_root, *, timeout=10, required_trace_bookkeeping=0):
    """Adopt only known installation custody; never replay application bytes.

    The returned socket uses the existing ``sockio.pump`` unchanged. No normal
    launcher selects this path yet. Keeper code/version is not backend identity;
    the custodian independently authenticates its current physical backend.
    """
    root = Path(runtime_root)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("custody adoption requires a known absolute installation root")
    if (
        not isinstance(bridge, str) or not bridge
        or type(timeout) not in (int, float)
        or not math.isfinite(timeout) or not 0 < timeout <= 60
        or type(required_trace_bookkeeping) is not int or required_trace_bookkeeping not in (0, 1)
    ):
        raise ValueError("custody adoption requires a bridge and bounded timeout")
    root = root.resolve()
    cfg = load_config(bridge, repository_root=Path.cwd())
    binding = Binding.issue(trace_bookkeeping=required_trace_bookkeeping)
    identity = binding.identity(cfg)
    context = capture_context(bridge)
    deadline = time.monotonic() + timeout
    connection, token = sockio._connect(sockio.absolute_handle(handle), timeout=timeout)
    try:
        if connection.family in (socket.AF_INET, socket.AF_INET6) and (
            not isinstance(token, str) or not token
        ):
            raise OSError("custody endpoint authentication is missing")
        reply = _exchange(connection, token, {"op": "ping"}, deadline)
        advertised = reply.get("installation_root")
        if (
            reply.get("ok") is not True or reply.get("pong") is not True
            or not supports(reply.get("custody_host")) or not supports_context(reply)
            or reply.get("ready") is not True or reply.get("draining") is not False
            or not isinstance(advertised, str) or not Path(advertised).is_absolute()
            or Path(advertised).resolve() != root
        ):
            raise execution_context.SharingUnavailable("custody host did not qualify")
        if required_trace_bookkeeping and not supports_trace_binding(reply.get("custody_host")):
            raise execution_context.SharingUnavailable("custody host cannot enforce checkpoint identity mode")
        fields = execution_context.fields_for_peer(cfg, reply)
        if required_trace_bookkeeping:
            fields["checkpoint_trace_bookkeeping"] = required_trace_bookkeeping
        if not hmac.compare_digest(binding.identity(cfg), identity):
            raise execution_context.SharingUnavailable("custody caller context changed")
        try:
            ack = _exchange(
                connection, token,
                {"op": "attach", "bridge": bridge, "custody": CONTRACT,
                 "installation_root": str(root), "configuration_context": context, **fields},
                deadline,
            )
            if (
                ack.get("ok") is not True or ack.get("attached") is not True
                or not supports(ack.get("custody"))
            ):
                raise ConnectionError("custody attach was not confirmed; no replay")
            if required_trace_bookkeeping and (
                not supports_trace_binding(ack.get("custody"))
                or type(ack.get("checkpoint_trace_bookkeeping")) is not int
                or ack["checkpoint_trace_bookkeeping"] != required_trace_bookkeeping
            ):
                raise ConnectionError("custody checkpoint identity mode was not confirmed; no replay")
            if not hmac.compare_digest(binding.identity(cfg), identity):
                raise ConnectionError("custody caller context changed after attach; no replay")
            connection.settimeout(None)
            return connection
        except ConnectionError:
            raise
        except (OSError, ValueError) as error:
            raise ConnectionError("custody attach outcome is unknown; no replay") from error
    except BaseException:
        sockio._close(connection)
        raise
