"""Optional report routes under the existing Gateway request and drain owner."""

from __future__ import annotations

import asyncio
import re

from fleet_manager.gateway_service_channel import BODY_BYTES
from fleet_manager.gateway_service_handler import RequestDisconnected

from .report_read import ReportUnavailable, parse_request
from .watch_replay_binding import ReplayBinding, ReplayBindingMismatch
from .watch_replay_store import ReplayCallerExpired, json_object


async def handle(gateway, request):
    from .watch_gateway import _response

    matched = re.fullmatch(r"/v1/read/([a-z0-9][a-z0-9-]{0,63})/([a-z]+)", request.target)
    if matched is None or matched[2] not in ("capabilities", "threads", "diff"):
        return _response(404, {"error": "report_route_not_found"})
    target, operation = matched.groups()
    key = request.context.principal, target
    if key not in gateway.bindings:
        return _response(403, {"error": "report_scope_refused"})
    owner = gateway.reports.get(key)
    if owner is None:
        return _response(404, {"error": "report_unsupported"})
    if request.method != "POST":
        return _response(405, {"error": "report_method_refused"})
    if len(request.body) > BODY_BYTES:
        return _response(413, {"error": "report_request_limit"})
    if dict(request.headers).get("content-type", "").split(";")[0] != "application/json":
        return _response(415, {"error": "report_json_required"})
    try:
        envelope = json_object(request.body)
        if set(envelope) != {"binding", "request"}:
            raise ValueError
        expected = ReplayBinding.from_document(envelope["binding"])
        payload = parse_request(operation, envelope["request"])
        if gateway._binding(request, target) != expected:
            raise ReplayBindingMismatch
    except ReplayBindingMismatch:
        return _response(409, {"error": "report_binding_mismatch"})
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        return _response(400, {"error": "invalid_report_request"})
    if gateway._closing or len(gateway._tasks) >= 8:
        return _response(503, {"error": "report_service_unavailable"})
    task = asyncio.create_task(execute(gateway, owner, operation, payload, request, target, expected))
    gateway._tasks.add(task)
    task.add_done_callback(gateway._completed)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        request.lifetime.close()
        raise


async def execute(gateway, owner, operation, payload, request, target, expected):
    from .watch_gateway import _response

    response = None
    publication_checkpoint = None

    def encode(value, current):
        nonlocal response, publication_checkpoint
        publication_checkpoint = current
        response = _response(200, dict(value, binding=expected.document()))
        if len(response.body) > BODY_BYTES:
            raise ReportUnavailable("report response exceeds transport limit")

    def checkpoint():
        gateway._check(request)
        if gateway.store.clock() >= request.context.expires_at:
            raise ReplayCallerExpired
        if gateway._binding(request, target) != expected:
            raise ReplayBindingMismatch
        if gateway.reports.get((request.context.principal, target)) is not owner:
            raise ReportUnavailable("report owner replaced")

    try:
        await asyncio.to_thread(
            owner.execute, operation, payload,
            expires_at=request.context.expires_at, checkpoint=checkpoint, response_validator=encode,
        )
        if publication_checkpoint is None:
            raise ReportUnavailable("report publication checkpoint unavailable")
        publication_checkpoint()
        return response
    except ReplayBindingMismatch:
        return _response(409, {"error": "report_binding_mismatch"})
    except (ReplayCallerExpired, PermissionError):
        return _response(403, {"error": "report_owner_refused"})
    except RequestDisconnected:
        return _response(503, {"error": "report_request_disconnected"})
    except Exception:
        return _response(503, {"error": "report_owner_unavailable"})
