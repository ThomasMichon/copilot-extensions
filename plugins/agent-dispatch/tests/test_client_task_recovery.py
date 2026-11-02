"""Synthetic HTTP contract tests for the task recovery client."""

from __future__ import annotations

import json

import httpx
import pytest

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.client_task_recovery import RECOVERY_OPERATIONS, WAKE_OUTBOXES

HEALTH = {
    "task_recovery": {
        "protocol": "task_recovery/v1",
        "operations": [*RECOVERY_OPERATIONS, "reconcile_wake"],
    }
}
FIELDS = {
    "expected_revision": "revision-a",
    "request_id": "request-a",
    "actor": "test-operator",
    "reason": "synthetic recovery",
}
WAKE_FIELDS = {
    "outbox": "wake_outbox",
    "wake_id": "wake-a",
    "expected_revision": "revision-a",
    "actor": "test-operator",
    "reason": "synthetic observation",
}


def _call(client: DispatchClient, method: str) -> dict:
    if method == "task_diagnostics":
        return client.task_diagnostics("task-a")
    if method == "recover_task":
        return client.recover_task("task-a", operation="detach_agent", **FIELDS)
    return client.reconcile_wake("task-a", **WAKE_FIELDS)


def test_diagnostics_preserves_redacted_snapshot_and_ordinary_auth() -> None:
    requests = []
    snapshot = {
        "contract": "task_recovery/v1",
        "task": {"status": "queued", "owner": None},
        "revision": "opaque-a",
        "attachments": [{"current": False}],
        "evidence": {"reservation": "unavailable"},
        "actions": {"detach_agent": {"available": False, "reason": "retained_execution"}},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=HEALTH if request.url.path == "/health" else snapshot)

    with DispatchClient(
        "https://dispatch.invalid",
        token="reader-fixture",
        control_token="control-fixture",
        transport=httpx.MockTransport(handle),
    ) as client:
        assert client.task_diagnostics("task-a") == snapshot
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/health"),
        ("GET", "/tasks/task-a/diagnostics"),
    ]
    assert all(r.headers["authorization"].endswith("reader-fixture") for r in requests)


@pytest.mark.parametrize("operation", RECOVERY_OPERATIONS)
def test_recovery_exact_route_body_and_control_auth(operation: str) -> None:
    requests = []
    result = {
        "operation": operation,
        "request_id": "request-a",
        "replayed": False,
        "task": {"status": "proposed"},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=HEALTH if request.method == "GET" else result)

    with DispatchClient(
        "https://dispatch.invalid",
        token="reader-fixture",
        control_token="control-fixture",
        transport=httpx.MockTransport(handle),
    ) as client:
        assert client.recover_task("task-a", operation=operation, **FIELDS) == result
    assert requests[0].url.path == "/health"
    assert requests[0].headers["authorization"].endswith("reader-fixture")
    assert requests[1].url.path == f"/tasks/task-a/recovery/{operation}"
    assert requests[1].headers["authorization"].endswith("control-fixture")
    assert json.loads(requests[1].content) == FIELDS


@pytest.mark.parametrize("outbox", WAKE_OUTBOXES)
def test_reconcile_sends_only_original_wake_selector(outbox: str) -> None:
    requests = []
    receipt = {"outcome": "sent", "receipt": {"send_id": "send-a", "sequence": 7}}

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=HEALTH if request.method == "GET" else receipt)

    with DispatchClient(
        "https://dispatch.invalid",
        control_token="control-fixture",
        transport=httpx.MockTransport(handle),
    ) as client:
        assert client.reconcile_wake("task-a", **{**WAKE_FIELDS, "outbox": outbox}) == receipt
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/health"),
        ("POST", "/tasks/task-a/wakes/reconcile"),
    ]
    assert json.loads(requests[-1].content) == {**WAKE_FIELDS, "outbox": outbox}
    assert requests[-1].headers["authorization"].endswith("control-fixture")


@pytest.mark.parametrize(
    "health",
    [
        None,
        [],
        {},
        {"task_recovery": None},
        {"task_recovery": "task_recovery/v1"},
        {"task_recovery": {"protocol": "task_recovery/v0", "operations": []}},
        {"task_recovery": {"protocol": "task_recovery/v1"}},
        {"task_recovery": {"protocol": "task_recovery/v1", "operations": "detach_agent"}},
        {"task_recovery": {"protocol": "task_recovery/v1", "operations": [None]}},
        {"task_recovery": {"protocol": "task_recovery/v1", "operations": [""]}},
    ],
)
@pytest.mark.parametrize("method", ["task_diagnostics", "recover_task", "reconcile_wake"])
def test_missing_old_or_malformed_health_has_no_effect(health: object, method: str) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=health)

    with DispatchClient(
        "https://dispatch.invalid", transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(DispatchUpgradeRequired, match="task_recovery/v1"):
            _call(client, method)
    assert [(r.method, r.url.path) for r in requests] == [("GET", "/health")]


@pytest.mark.parametrize("failure", ["http", "transport", "json"])
def test_unavailable_health_is_upgrade_required_without_mutation(failure: str) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if failure == "transport":
            raise httpx.ReadTimeout("fixture health unavailable", request=request)
        if failure == "json":
            return httpx.Response(200, content=b"not json")
        return httpx.Response(503, json={"detail": "fixture unavailable"})

    with DispatchClient(
        "https://dispatch.invalid", transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(DispatchUpgradeRequired, match="cannot confirm"):
            _call(client, "recover_task")
    assert len(requests) == 1
    assert requests[0].method == "GET"


@pytest.mark.parametrize(
    "method,operation",
    [
        ("recover_task", "detach_agent"),
        ("reconcile_wake", "reconcile_wake"),
    ],
)
def test_operation_must_be_advertised(method: str, operation: str) -> None:
    requests = []
    health = {"task_recovery": {"protocol": "task_recovery/v1", "operations": []}}

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=health)

    with DispatchClient(
        "https://dispatch.invalid", transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(DispatchUpgradeRequired, match=operation):
            _call(client, method)
    assert len(requests) == 1


def test_health_is_rechecked_after_provider_rollback() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=HEALTH if len(requests) == 1 else {})

    with DispatchClient(
        "https://dispatch.invalid", transport=httpx.MockTransport(handle)
    ) as client:
        client.task_diagnostics("task-a")
        with pytest.raises(DispatchUpgradeRequired):
            _call(client, "recover_task")
    assert [r.method for r in requests] == ["GET", "GET", "GET"]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("invalid", ["", "  ", None, 1])
def test_invalid_recovery_fields_fail_before_any_request(field: str, invalid: object) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"invalid input contacted {request.url.path}")

    with DispatchClient(
        "https://dispatch.invalid", transport=httpx.MockTransport(unexpected)
    ) as client:
        with pytest.raises(ValueError, match=field):
            client.recover_task("task-a", operation="reset_queued", **{**FIELDS, field: invalid})


@pytest.mark.parametrize("status", [404, 409, 503])
@pytest.mark.parametrize(
    "method,route",
    [
        ("recover_task", "/tasks/task-a/recovery/detach_agent"),
        ("reconcile_wake", "/tasks/task-a/wakes/reconcile"),
    ],
)
def test_refused_recovery_is_not_retried_or_replaced(
    status: int,
    method: str,
    route: str,
) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return (
            httpx.Response(200, json=HEALTH)
            if request.method == "GET"
            else httpx.Response(status, json={"detail": "retained execution"})
        )

    with DispatchClient(
        "https://dispatch.invalid", transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(DispatchError) as caught:
            _call(client, method)
    assert caught.value.status_code == status
    assert [r.url.path for r in requests] == ["/health", route]


@pytest.mark.parametrize("field", WAKE_FIELDS)
def test_blank_wake_fields_fail_before_any_request(field: str) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"invalid input contacted {request.url.path}")

    with DispatchClient(
        "https://dispatch.invalid",
        transport=httpx.MockTransport(unexpected),
    ) as client:
        with pytest.raises(ValueError):
            client.reconcile_wake("task-a", **{**WAKE_FIELDS, field: " "})


def test_task_only_client_cannot_manufacture_control_authority() -> None:
    from agent_dispatch.task_scope import TASK_SCOPE_HEADER

    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json=HEALTH)
        return httpx.Response(403, json={"detail": "control authority required"})

    with DispatchClient(
        "https://dispatch.invalid",
        task_token="task-fixture",
        transport=httpx.MockTransport(handle),
    ) as client:
        with pytest.raises(DispatchError) as caught:
            _call(client, "recover_task")
    assert caught.value.status_code == 403
    assert len(requests) == 2
    assert all("authorization" not in request.headers for request in requests)
    assert all(request.headers[TASK_SCOPE_HEADER] == "task-fixture" for request in requests)


def test_invalid_operations_and_caller_outcomes_are_not_accepted() -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"invalid input contacted {request.url.path}")

    with DispatchClient(
        "https://dispatch.invalid", transport=httpx.MockTransport(unexpected)
    ) as client:
        with pytest.raises(ValueError, match="operation"):
            client.recover_task("task-a", operation="force_clear", **FIELDS)
        with pytest.raises(ValueError, match="outbox"):
            client.reconcile_wake("task-a", **{**WAKE_FIELDS, "outbox": "other"})
        for field in ("outcome", "send_token", "request_id"):
            with pytest.raises(TypeError):
                client.reconcile_wake("task-a", **WAKE_FIELDS, **{field: "caller-label"})


def test_legacy_reset_and_target_detach_do_not_require_new_health() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"status": "proposed"})

    with DispatchClient(
        "https://dispatch.invalid", transport=httpx.MockTransport(handle)
    ) as client:
        client.reset("task-a", reason="legacy", expected_status="started")
        client.detach("task-a")
    assert [(r.method, r.url.path) for r in requests] == [
        ("POST", "/tasks/task-a/reset"),
        ("POST", "/tasks/task-a/detach"),
    ]
    assert json.loads(requests[0].content) == {
        "reason": "legacy",
        "expected_status": "started",
        "expected_generation": None,
        "expected_owner_session_id": None,
    }
