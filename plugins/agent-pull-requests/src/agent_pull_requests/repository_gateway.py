"""Canonical bounded handler operations for an admitted local repository profile."""

import asyncio
import json
import re
import time

from fleet_manager.gateway_service_channel import BODY_BYTES
from fleet_manager.gateway_service_handler import BoundedServiceResponse, RequestDisconnected
from single_instance_lease import OperationLockedError

from .repository_subscriptions import (
    OPERATIONS, RepositoryHistoryGap, RepositoryRequestConflict, RepositorySubscriptions,
    contract,
)
from .watch_replay_store import ReplayCallerExpired, json_object
from .repository_contract import RepositoryIdentity

PATH = re.compile(r"/v1/(repositories|populations)/([a-z0-9][a-z0-9-]{0,63})/([a-z]+)")


def profile(owner):
    if owner.store.repository_filtered:
        from . import filtered_subscriptions
        return "populations", filtered_subscriptions.OPERATIONS, (
            filtered_subscriptions.FilteredSubscriptions
        ), filtered_subscriptions.contract
    return "repositories", OPERATIONS, RepositorySubscriptions, contract


def response(status, value):
    return BoundedServiceResponse(
        status, (("content-type", "application/json"), ("cache-control", "no-store")),
        json.dumps(value, allow_nan=False).encode(),
    )


def admitted_operation(owner, request, operation):
    """Wait for shared transaction custody only within the original caller budget."""
    while True:
        owner.store.check_current(request.context.expires_at, lambda: owner._check(request))
        try:
            return operation()
        except OperationLockedError:
            time.sleep(.01)


async def handle(owner, request):
    """Use only the transport-derived principal and existing admitted scope map."""
    route, operations, subscriptions, declaration = profile(owner)
    if request.target == f"/v1/{route}":
        return await listing(owner, request)
    matched = PATH.fullmatch(request.target)
    if matched is None or matched[1] != route:
        return response(404, {"error": "repository_route_not_found"})
    _, target, operation = matched.groups()
    scope = owner.bindings.get((request.context.principal, target))
    if scope is None or owner.repositories is None or not owner.store.repository_continuing:
        return response(403, {"error": "repository_scope_refused"})
    identity = owner.repositories.identities.get(scope.account)
    if identity is None or identity.repo != scope.repo:
        return response(403, {"error": "repository_binding_refused"})
    if owner._closing:
        return response(503, {"error": "repository_service_draining"})
    binding = {"protocol": declaration()["protocol"], "namespace": owner.store.namespace,
               "repository_key": identity.key, "target": target}
    if operation == "capabilities":
        if request.method != "GET" or request.body:
            return response(400, {"error": "invalid_repository_capabilities"})
        extra = {"driver": owner.repositories.capabilities[identity.key]} if (
            owner.store.repository_filtered
        ) else {}
        return response(200, {**binding, **declaration(), "identity": identity.document(), **extra})
    if operation not in operations or request.method != "POST":
        return response(405, {"error": "repository_operation_refused"})
    if len(request.body) > BODY_BYTES:
        return response(413, {"error": "repository_request_limit"})
    if dict(request.headers).get("content-type", "").split(";")[0] != "application/json":
        return response(415, {"error": "repository_json_required"})
    try:
        value = json_object(request.body)
        if set(value) != {"protocol", "namespace", "repository_key", "request"} or (
            not isinstance(value["request"], dict)
        ):
            raise ValueError
        if any(value[name] != binding[name] for name in ("protocol", "namespace", "repository_key")):
            return response(409, {"error": "repository_binding_mismatch"})
    except (ValueError, UnicodeError, RecursionError):
        return response(400, {"error": "invalid_repository_request"})
    if len(owner._tasks) >= 8:
        return response(503, {"error": "repository_service_busy"})

    def execute():
        def checkpoint():
            owner._check(request)
            if request.context.namespace != owner.store.namespace:
                raise RequestDisconnected()
        checkpoint()
        return admitted_operation(owner, request, lambda: (
            subscriptions(owner.repositories).execute(
                identity.key, scope.consumer, operation, value["request"],
                expires_at=request.context.expires_at, checkpoint=checkpoint,
            )
        ))

    async def dispatch():
        try:
            result = await asyncio.to_thread(execute)
            owner._check(request)
            selected = response(200, {**binding, "result": result})
            if len(selected.body) > request.response_limit:
                return response(413, {"error": "repository_response_limit", "outcome": "uncertain"})
            return selected
        except RepositoryHistoryGap:
            return response(410, {"error": "repository_history_gap", "resync": "query_then_subscribe"})
        except RepositoryRequestConflict:
            return response(409, {"error": "repository_request_conflict"})
        except KeyError:
            return response(404, {"error": "repository_registration_not_found"})
        except (RequestDisconnected, ReplayCallerExpired, OperationLockedError):
            return response(503, {"error": "repository_request_unavailable", "outcome": "uncertain"})
        except (ValueError, UnicodeError, RecursionError):
            return response(400, {"error": "invalid_repository_request"})

    task = asyncio.create_task(dispatch())
    owner._tasks.add(task)
    task.add_done_callback(owner._completed)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        request.lifetime.close()
        raise


async def listing(owner, request):
    """List only admitted configured repositories from one cache transaction."""
    if request.method != "GET" or request.body:
        return response(400, {"error": "invalid_repository_listing"})
    if owner._closing or len(owner._tasks) >= 8 or owner.repositories is None:
        return response(503, {"error": "repository_service_unavailable"})
    selected = {
        target: scope.account for (principal, target), scope in owner.bindings.items()
        if principal == request.context.principal and scope.account in owner.repositories.identities
    }
    def execute():
        def read(document):
            cached = {
                RepositoryIdentity.parse(row["identity"]).key: row
                for row in document["repository_cache"]["repositories"]
            }
            return [{
                "target": target, "identity": owner.repositories.identities[key].document(),
                "inventory": cached[key]["inventory"] if key in cached else {"outcome": "unknown"},
                "known_members": (
                    len(cached[key]["pulls"]) if owner.store.repository_filtered
                    else sum(pull["member"] for pull in cached[key]["pulls"])
                ) if key in cached else 0,
            } for target, key in selected.items()]
        return admitted_operation(owner, request, lambda: (
            owner.store._review_transaction(
                read, persist=False, expires_at=request.context.expires_at,
                checkpoint=lambda: owner._check(request),
            )
        ))
    async def dispatch():
        try:
            result = await asyncio.to_thread(execute)
            owner._check(request)
            return response(200, {
                **profile(owner)[3](), "namespace": owner.store.namespace, "repositories": result,
            })
        except (RequestDisconnected, ReplayCallerExpired, OperationLockedError):
            return response(503, {"error": "repository_request_unavailable"})
    task = asyncio.create_task(dispatch())
    owner._tasks.add(task)
    task.add_done_callback(owner._completed)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        request.lifetime.close()
        raise
