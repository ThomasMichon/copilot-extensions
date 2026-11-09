"""Optional report routes under the existing Gateway request and drain owner."""

from __future__ import annotations

import asyncio
import json
import re

from fleet_manager.gateway_service_channel import BODY_BYTES
from fleet_manager.gateway_service_handler import RequestDisconnected

from .report_read import ReportUnavailable, parse_request, report_scope, response_budget
from .watch_replay_binding import ReplayBinding, ReplayBindingMismatch
from .watch_replay_store import ReplayCallerExpired, json_object


def _failure(error):
    from .watch_gateway import _response

    if isinstance(error, ReplayBindingMismatch):
        return _response(409, {"error": "report_binding_mismatch"})
    if isinstance(error, (ReplayCallerExpired, PermissionError)):
        return _response(403, {"error": "report_owner_refused"})
    if isinstance(error, RequestDisconnected):
        return _response(503, {"error": "report_request_disconnected"})
    return _response(503, {"error": "report_owner_unavailable"})


async def handle(gateway, request):
    from .watch_gateway import _response

    matched = re.fullmatch(r"/(v[12])/read/([a-z0-9][a-z0-9-]{0,63})/([a-z]+)", request.target)
    if matched is None or matched[3] not in ("capabilities", "threads", "diff"):
        return _response(404, {"error": "report_route_not_found"})
    version, target, operation = matched.groups()
    key = request.context.principal, target
    if key not in gateway.bindings:
        return _response(403, {"error": "report_scope_refused"})
    owner = gateway.reports.get(key)
    if owner is None:
        return _response(404, {"error": "report_unsupported"})
    limit = BODY_BYTES
    if version == "v2":
        try:
            # Old SDKs and local embeddings cannot select this request budget.
            from fleet_manager.gateway_service_handler import BoundedServiceResponse

            limit = response_budget(request.response_limit)
            if not callable(BoundedServiceResponse) or limit <= BODY_BYTES or limit != owner.response_limit:
                raise ValueError
        except (ImportError, AttributeError, ValueError):
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
        wire_binding = envelope["binding"]
        if not isinstance(wire_binding, dict) or "report_scope" not in wire_binding:
            raise ValueError
        expected_scope = report_scope(wire_binding["report_scope"])
        expected = ReplayBinding.from_document({
            name: value for name, value in wire_binding.items() if name != "report_scope"
        })
        payload = parse_request(operation, envelope["request"], response_limit=limit)
        if gateway._binding(request, target) != expected or dict(owner.scope) != expected_scope:
            raise ReplayBindingMismatch
    except ReplayBindingMismatch:
        return _response(409, {"error": "report_binding_mismatch"})
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        return _response(400, {"error": "invalid_report_request"})
    if gateway._closing or len(gateway._tasks) >= 8:
        return _response(503, {"error": "report_service_unavailable"})
    prepared = asyncio.get_running_loop().create_future()
    handoff = asyncio.Event()
    final_checks = []
    task = asyncio.create_task(execute(
        gateway, owner, operation, payload, request, target, expected, expected_scope, prepared, handoff,
        final_checks, limit,
    ))
    gateway._tasks.add(task)
    task.add_done_callback(gateway._completed)
    try:
        await asyncio.shield(prepared)
        handoff.set()
        response = await asyncio.shield(task)
        gateway._check(request)
        if response.status == 200:
            if len(final_checks) != 1:
                raise ReportUnavailable("report publication checkpoint unavailable")
            final_checks[0]()
        return response
    except asyncio.CancelledError:
        request.lifetime.close()
        raise
    except Exception as error:
        return _failure(error)
    finally:
        handoff.set()


async def execute(gateway, owner, operation, payload, request, target, expected, expected_scope,
                  prepared, handoff, final_checks, limit):
    from .watch_gateway import _response

    response = None
    publication_checkpoint = None

    def encode(value, current):
        nonlocal response, publication_checkpoint
        publication_checkpoint = current
        body = json.dumps(dict(value, binding=expected.document()), allow_nan=False).encode("utf-8")
        if len(body) > limit:
            raise ReportUnavailable("report response exceeds transport limit")
        if limit > BODY_BYTES:
            from fleet_manager.gateway_service_handler import BoundedServiceResponse
            response = BoundedServiceResponse(
                200, (("content-type", "application/json"), ("cache-control", "no-store")), body,
            )
        else:
            response = _response(200, dict(value, binding=expected.document()))

    def checkpoint():
        gateway._check(request)
        if gateway.store.clock() >= request.context.expires_at:
            raise ReplayCallerExpired
        if gateway._binding(request, target) != expected or dict(owner.scope) != expected_scope:
            raise ReplayBindingMismatch
        if gateway.reports.get((request.context.principal, target)) is not owner:
            raise ReportUnavailable("report owner replaced")
        if limit > BODY_BYTES and (
            owner.response_limit != limit or request.response_limit != limit
        ):
            raise ReportUnavailable("report response selection changed")

    try:
        await asyncio.to_thread(
            owner.execute, operation, payload,
            expires_at=request.context.expires_at, checkpoint=checkpoint, response_validator=encode,
            publication_validator=final_checks.append,
            response_limit=limit,
        )
        if publication_checkpoint is None:
            raise ReportUnavailable("report publication checkpoint unavailable")
        await asyncio.to_thread(publication_checkpoint)
        checkpoint()
        prepared.set_result(response)
        await handoff.wait()
        await asyncio.to_thread(publication_checkpoint)
        checkpoint()
        return response
    except Exception as error:
        response = _failure(error)
        if not prepared.done():
            prepared.set_result(response)
        return response
