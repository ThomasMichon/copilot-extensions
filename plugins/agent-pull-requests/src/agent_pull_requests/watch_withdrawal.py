"""Durable stop custody, distinct from successor promotion and PID cleanup."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from uuid import UUID, uuid4

from zdd import publication_history, routing
from zdd.process_exit import observe_original_exit

from .watch_custody import endpoint_tuple, require_absent
from .watch_journal import (
    _controller, _controller_held, _json_object, _persist, _private_bytes,
    _require_cell_history, _selection, _terminal_custody,
)
from .watch_private import confirm_private
from .watch_runtime import WatchInstance

_FILE = "watch-owner-withdrawal.json"


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _route_body(value: str) -> dict:
    return _json_object(bytes.fromhex(value))


def _withdrawn(prior: dict) -> dict:
    table = _route_body(prior["route"])
    endpoint = routing.Endpoint.from_dict(table.get("active", {}))
    if endpoint is None or endpoint.to_dict() != table["active"]:
        raise PermissionError("watch withdrawal has no exact native endpoint")
    if publication_history.read(table) is None:
        raise PermissionError("watch withdrawal requires captured publication history")
    return {**{key: value for key, value in table.items() if key != "active"},
            "previous": endpoint.to_dict()}


def _archive_path(installation, route: dict):
    return installation.run_root / "watch-owner-withdrawals" / f"{_digest(route)}.json"


def _validate(installation, document: dict) -> dict:
    installation.require_current(environment={})
    if set(document) != {
        "schema", "transactionId", "authorization", "installation", "controller",
        "originId", "originDigest", "instance", "endpoint", "prior", "phase",
    } or document["schema"] != 1 or type(document["schema"]) is not int:
        raise ValueError("watch withdrawal custody has invalid fields")
    _require_cell_history(installation, document["installation"])
    for key in ("transactionId", "originId"):
        if str(UUID(document[key])) != document[key]:
            raise ValueError("watch withdrawal identity is invalid")
    if (
        document["phase"] not in {"requested", "complete"}
        or not isinstance(document["authorization"], str)
        or len(document["authorization"]) != 64
        or any(char not in "0123456789abcdef" for char in document["authorization"])
    ):
        raise ValueError("watch withdrawal capability or phase is invalid")
    instance = WatchInstance.from_identity_evidence(
        document["instance"], installation, environment={},
    )
    endpoint = endpoint_tuple(document["endpoint"])
    raw = _private_bytes(
        installation.run_root / "watch-owner-history" / f'{document["originId"]}.json',
    )
    if raw is None:
        raise PermissionError("watch withdrawal has no archived terminal origin")
    origin = _json_object(raw)
    if (
        origin.get("phase") not in {"complete", "rolled-back"}
        or origin.get("transactionId") != document["originId"]
        or _digest(_terminal_custody(origin)) != document["originDigest"]
    ):
        raise PermissionError("watch withdrawal terminal origin changed")
    role = "successor" if origin["phase"] == "complete" else "predecessor"
    if origin[role] != instance.evidence() or origin["endpoints"][role] != document["endpoint"]:
        raise PermissionError("watch withdrawal differs from exact terminal owner")
    prior = document["prior"]
    if not isinstance(prior, dict) or set(prior) != {"current", "lastKnownGood", "manifest", "route"}:
        raise ValueError("watch withdrawal selection custody is invalid")
    withdrawn = _withdrawn(prior)
    active = _route_body(prior["route"])["active"]
    if (
        active["pid"] != instance.pid or active["process_start_time"] != instance.start_time
        or active.get("version") != instance.runtime.runtime_version
        or active["bind"] != endpoint[0] or active["port"] != endpoint[1]
    ):
        raise PermissionError("watch withdrawal native route differs from exact owner")
    if role == "predecessor" and origin["rollbackRoute"] is not None:
        from .watch_rollback import _body, restoration, validate_restoration

        validate_restoration(origin)
        _, expected = restoration(origin)
        route_matches = _body(_route_body(prior["route"])) == expected
    else:
        expected_route = origin["targetRoute"] if role == "successor" else origin["prior"]["route"]
        route_matches = expected_route is not None and (
            _route_body(expected_route) == _route_body(prior["route"])
        )
    if not route_matches:
        raise PermissionError("watch withdrawal route differs from archived terminal custody")
    if role == "successor":
        version = instance.runtime.runtime_version
        if prior["manifest"] != origin["targetManifest"] or any(
            prior[key] is None or bytes.fromhex(prior[key]).decode("utf-8").strip() != version
            for key in ("current", "lastKnownGood")
        ):
            raise PermissionError("watch withdrawal selection differs from terminal target")
    elif any(prior[key] != origin["prior"][key] for key in ("current", "lastKnownGood", "manifest")):
        raise PermissionError("watch withdrawal selection differs from restored predecessor")
    if not withdrawn.get("previous"):
        raise PermissionError("watch withdrawal lost its generation witness")
    return document


def read(installation, origin: dict) -> dict | None:
    """An older completed stop is inert; an unresolved stop always blocks."""
    raw = _private_bytes(installation.run_root / _FILE)
    if raw is None:
        return None
    document = _validate(installation, _json_object(raw))
    if document["originId"] == origin["transactionId"]:
        return document
    if document["phase"] != "complete":
        raise PermissionError("another watch withdrawal requires reconciliation")
    require_withdrawn_route(installation, _withdrawn(document["prior"]))
    return None


def require_withdrawn_route(installation, table: dict, *, probe_writer: bool = False) -> None:
    """Only an archived completed withdrawal licenses cold route reuse."""
    raw = _private_bytes(_archive_path(installation, table))
    if raw is None:
        raise PermissionError("watch cold route has no completed withdrawal custody")
    document = _validate(installation, _json_object(raw))
    if document["phase"] != "complete" or _withdrawn(document["prior"]) != table:
        raise PermissionError("watch cold route differs from completed withdrawal")
    instance = document["instance"]
    if observe_original_exit(instance["pid"], instance["startTime"]) is not True:
        raise PermissionError("watch withdrawn original exit is not proven")
    if probe_writer:
        require_absent(installation)


def _require_selection(installation, document: dict, *, withdrawn: bool = False) -> None:
    selected = _selection(installation)
    if any(selected[key] != document["prior"][key] for key in ("current", "lastKnownGood", "manifest")):
        raise PermissionError("watch withdrawal selection changed")
    actual = selected["route"]
    expected = _withdrawn(document["prior"]) if withdrawn else _route_body(document["prior"]["route"])
    if actual is None or _route_body(actual) != expected:
        raise PermissionError("watch withdrawal route changed")


def authorize_withdrawal(installation, instance, *, transaction_id: str, authorization: str) -> None:
    raw = _private_bytes(installation.run_root / _FILE)
    if raw is None:
        raise PermissionError("watch stop has no durable withdrawal intent")
    document = _validate(installation, _json_object(raw))
    if (
        document["installation"] != installation.evidence() or document["phase"] != "requested"
        or document["transactionId"] != transaction_id
        or not isinstance(authorization, str)
        or not hmac.compare_digest(document["authorization"], authorization)
        or document["instance"] != instance.evidence() or instance.pid != os.getpid()
    ):
        raise PermissionError("watch stop lacks exact withdrawal authorization")
    _controller_held(installation, document["controller"], None)
    _require_selection(installation, document)
    instance.require_live(
        installation, read_health=lambda: {"instance": instance.evidence()}, environment={},
    )


def finish(invocation, guard, origin: dict, *, client=None, timeout: float) -> None:
    """Reconcile an ambiguous stop, retaining custody until original exit and CAS."""
    from .watch_control import WatchControlClient
    from .watch_notification import positive_seconds

    deadline = time.monotonic() + positive_seconds(timeout)
    invocation.require_current()

    def remaining():
        guard.require_owned()
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("watch withdrawal completion deadline expired")
        return left

    installation = invocation.fresh_installation()
    _controller_held(installation, _controller(), guard)
    remaining()
    known_bytes = _private_bytes(installation.run_root / _FILE)

    def persist_current(value):
        nonlocal known_bytes
        remaining()
        _controller_held(installation, _controller(), guard)
        installation.require_current(environment={})
        if _private_bytes(installation.run_root / _FILE) != known_bytes:
            raise PermissionError("watch withdrawal custody changed before replacement")
        _persist(installation.run_root / _FILE, value, replace=known_bytes is not None)
        known_bytes = _private_bytes(installation.run_root / _FILE)
        if known_bytes is None or _json_object(known_bytes) != value:
            raise PermissionError("watch withdrawal custody changed during replacement")

    document = read(installation, origin)
    if document is None:
        if client is None:
            raise PermissionError("watch withdrawal requires an attributable live owner")
        document = {
            "schema": 1, "transactionId": str(uuid4()), "authorization": secrets.token_hex(32),
            "installation": installation.evidence(), "controller": _controller(),
            "originId": origin["transactionId"], "originDigest": _digest(_terminal_custody(origin)),
            "instance": client.instance.evidence(), "endpoint": origin["endpoints"][
                "successor" if origin["phase"] == "complete" else "predecessor"
            ], "prior": _selection(installation), "phase": "requested",
        }
        _validate(installation, document)
        persist_current(document)
    elif document["phase"] == "requested":
        if document["installation"] != installation.evidence():
            raise PermissionError("pending watch withdrawal requires its original installation receipt")
        document.update(controller=_controller(), authorization=secrets.token_hex(32))
        persist_current(document)
    instance = WatchInstance.from_evidence(document["instance"], installation, environment={})
    expected_route = _withdrawn(document["prior"])
    if document["phase"] != "complete":
        if observe_original_exit(instance.pid, instance.start_time) is not True:
            _require_selection(installation, document)
            client = WatchControlClient(
                endpoint=endpoint_tuple(document["endpoint"]), instance=instance,
                installation=invocation.fresh_installation,
                transaction=lambda: (document["transactionId"], document["authorization"]),
            )
            reference = client.capture_exit(timeout=remaining())
            try:
                client.control("withdraw", timeout=remaining())
                if not reference.wait(min(30.0, remaining())):
                    raise TimeoutError("watch withdrawn original process has not exited")
            finally:
                reference.close()

        def require_exit(raw):
            remaining()
            current = read(installation, origin)
            if current != document:
                raise PermissionError("watch withdrawal changed before native mutation")
            _require_selection(installation, document)
            if observe_original_exit(instance.pid, instance.start_time) is not True:
                raise PermissionError("watch withdrawal lacks original native exit")
            require_absent(installation)
            return None

        table = routing.read_table(installation.run_root, strict=True)
        if table != expected_route:
            active = routing.Endpoint.from_dict(_route_body(document["prior"]["route"])["active"])
            routing.withdraw_active_guarded(
                installation.run_root, expected_active=active, refuse_current=require_exit,
                lock_deadline=deadline,
            )
        remaining()
        _require_selection(installation, document, withdrawn=True)
        if observe_original_exit(instance.pid, instance.start_time) is not True:
            raise PermissionError("watch withdrawal original exit remains unproven")
        require_absent(installation)
        document["phase"] = "complete"
        persist_current(document)
    remaining()
    _require_selection(installation, document, withdrawn=True)
    path = _archive_path(installation, expected_route)
    archived = _private_bytes(path)
    if archived is None:
        installation.require_current(environment={})
        _persist(path, document, replace=False)
    elif _json_object(archived) != document:
        raise PermissionError("watch withdrawal archive changed")
    else:
        confirm_private(path)
    require_withdrawn_route(installation, expected_route, probe_writer=True)
