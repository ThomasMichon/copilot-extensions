"""Fresh-call generation admission through the installed publication owner."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import sockio

CONTRACT = json.loads(Path(__file__).with_name("incoming-contract.json").read_text(encoding="utf-8"))


class AdmissionRefused(OSError):
    """The launched physical generation is no longer admitted for new work."""


class OutcomeUnknown(OSError):
    """Application bytes may have been sent; do not replay through a cold path."""


def validate_call_reply(reply):
    if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
        raise OutcomeUnknown("warm call reply is invalid; no cold replay")
    if "shared_execution" in reply:
        if type(reply["shared_execution"]) is not bool or (
            reply["shared_execution"] is False and (
                reply["ok"] or reply.keys() - {"ok", "error", "shared_execution"}
            )
        ):
            raise OutcomeUnknown("warm call refusal contradicts its outcome; no cold replay")
    if reply["ok"]:
        if (
            not {"content", "structured"} & reply.keys()
            or ("content" in reply and reply["content"] is not None and not isinstance(reply["content"], str))
            or ("isError" in reply and type(reply["isError"]) is not bool)
        ):
            raise OutcomeUnknown("warm call result shape is invalid; no cold replay")
    elif not isinstance(reply.get("error"), str):
        raise OutcomeUnknown("warm call error shape is invalid; no cold replay")
    return reply


def require_selected():
    if launch_generation() is None:
        return None
    from .cutover import _installed_slot

    try:
        return _installed_slot()
    except OSError as exc:
        raise AdmissionRefused("physical generation is not selected; no work accepted") from exc


def supports(value):
    return isinstance(value, dict) and value.get("name") == CONTRACT["name"] \
        and type(value.get("minimum")) is int and type(value.get("maximum")) is int \
        and value["minimum"] <= 1 <= value["maximum"]


@dataclass(frozen=True)
class Admission:
    generation: str
    root: str


def ping_reply(server) -> dict:
    from .execution_context import descriptor

    return {
        "ok": True, "pong": True, "sessions": server.pool.size, "attached": server._attached,
        "pid": os.getpid(), "version": server._version,
        "runtime_root": str(server._runtime_root) if server._runtime_root else None,
        "fresh_admission": CONTRACT, "draining": server._draining,
        "ready": server._server is not None and server._server.is_serving(),
        "execution_context": descriptor(),
    }


def accepts(server, request) -> bool:
    """Revalidate fresh admission before accepting any application effects."""
    from . import source_serving

    expected = request.get("generation")
    root = request.get("runtime_root")
    return (
        (server._runtime_root is None or source_serving.generation_selected(server))
        and
        (expected is None or (
            expected == server._version and source_serving.generation_selected(server)
        ))
        and (root is None or (
            isinstance(root, str) and server._runtime_root is not None
            and Path(root).resolve() == server._runtime_root.resolve()
        ))
    )


def launch_generation() -> str | None:
    """Resolve the actual immutable launch slot, independently of package labels."""
    from agent_procutil._daemon_instance_identity import runtime_slot_paths

    layout = runtime_slot_paths(Path(sys.prefix))
    if not Path(__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
        return None
    return layout[0] if layout is not None else None


def _ping(handle) -> dict:
    connection, token = sockio._connect(handle)
    try:
        connection.settimeout(2)
        request = {"op": "ping"}
        if token is not None:
            request["token"] = token
        connection.sendall((json.dumps(request) + "\n").encode())
        try:
            response = json.loads(sockio._read_ack_line(connection))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OSError("serving peer returned an invalid readiness reply") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise OSError("serving peer did not qualify")
        return response
    finally:
        sockio._close(connection)


def ensure_current(handle) -> Admission | None:
    """Never use a stale graph for a fresh call, including after skipped activation."""
    generation = launch_generation()
    if generation is None:
        return None  # standalone/source clients retain their existing exact-handle contract
    from .cutover import _require_selected, _run_slot_startup

    layout = require_selected()
    root = str(layout[2])

    def qualified(reply):
        return reply.get("version") == generation and reply.get("runtime_root") == root \
            and supports(reply.get("fresh_admission")) \
            and reply.get("ready") is True and not reply.get("draining")

    try:
        reply = _ping(handle)
    except OSError:
        reply = {}
    other_root = reply.get("runtime_root")
    if other_root is not None and not isinstance(other_root, str):
        raise OSError("serving peer returned an invalid installation identity")
    if other_root is not None and Path(other_root).resolve() != layout[2].resolve():
        raise OSError("serving handle belongs to another installation; no takeover attempted")
    if not qualified(reply):
        from .forward import _ensure_serve_enabled

        if not _ensure_serve_enabled() or os.environ.get("AGENT_MCP_NO_CUTOVER"):
            raise OSError("current serving generation is unavailable; stale execution refused")
        from zdd import CutoverLockedError

        # The callback owns actual slot/native readiness and compare-before-publish.
        # No predecessor drain, census, reset, or fabricated layout is involved.
        try:
            _run_slot_startup(layout, generation, health_timeout=15, fixed_socket=handle)
        except CutoverLockedError as exc:
            raise OSError("serving startup is busy before attach; shared execution unavailable") from exc
        reply = _ping(handle)
    try:
        _require_selected(layout)
    except OSError as exc:
        raise AdmissionRefused("physical selection changed; no work accepted") from exc
    if not qualified(reply):
        raise OSError("incoming serving generation did not qualify")
    return Admission(generation, root)


def attach(handle, bridge):
    admission = ensure_current(handle)
    if admission is None:
        return sockio.attach(handle, bridge)
    return sockio.attach(
        handle, bridge, expected_generation=admission.generation, expected_root=admission.root,
    )
