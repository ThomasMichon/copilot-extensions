"""Recovery MCP registration and client forwarding without servers or private state."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_dispatch.client import DispatchClient, DispatchUpgradeRequired
from agent_dispatch.mcp_server import DispatchTools, build_server

HEALTH = {
    "task_recovery": {
        "protocol": "task_recovery/v1",
        "operations": ["detach_agent", "reset_proposed", "reset_queued", "reconcile_wake"],
    }
}
FIELDS = {
    "expected_revision": "revision-a",
    "request_id": "request-a",
    "actor": "test-operator",
    "reason": "synthetic recovery",
}


def test_falsey_explicit_client_factory_is_not_replaced():
    class Factory:
        def __bool__(self):
            return False

        def __call__(self):
            return DispatchClient("https://dispatch.invalid", transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json=HEALTH if request.url.path == "/health" else {"fixture": True},
                ),
            ))

    factory = Factory()
    tools = DispatchTools(client_factory=factory)
    assert tools._client_factory is factory
    assert tools._observation_client_factory is factory
    assert tools._recovery_client_factory is factory
    assert tools.task_diagnostics("task-a") == {"fixture": True}
    assert tools.detach_agent("task-a", **FIELDS) == {"fixture": True}


@pytest.fixture
def tools_and_requests() -> tuple[DispatchTools, list[httpx.Request]]:
    recorded: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200, json=HEALTH if request.url.path == "/health" else {"fixture": True}
        )

    tools = DispatchTools(
        client_factory=lambda: DispatchClient(
            "https://dispatch.invalid",
            control_token="control-fixture",
            transport=httpx.MockTransport(handle),
        ),
        identity_resolver=lambda: pytest.fail("recovery must not invent an actor from identity"),
        repo_resolver=lambda: pytest.fail(
            "task recovery must not require fleet listing or a lane"
        ),
    )
    return tools, recorded


def test_real_mcp_registration_has_explicit_recovery_schemas(
    tools_and_requests: tuple[DispatchTools, list[httpx.Request]],
) -> None:
    pytest.importorskip("mcp", reason="requires the optional 'mcp' extra")
    tools, requests = tools_and_requests
    registered = asyncio.run(build_server(tools).list_tools())
    schemas = {tool.name: tool.input_schema for tool in registered}
    assert {
        "dispatch_task_diagnostics",
        "dispatch_recover_task",
        "dispatch_detach_agent",
        "dispatch_reset",
        "dispatch_reconcile_wake",
        "dispatch_detach",
    } <= schemas.keys()
    assert set(schemas["dispatch_detach_agent"]["required"]) == {"task_id", *FIELDS}
    reconcile = schemas["dispatch_reconcile_wake"]
    assert set(reconcile["required"]) == {
        "task_id",
        "outbox",
        "wake_id",
        "expected_revision",
        "actor",
        "reason",
    }
    assert set(reconcile["properties"]) == set(reconcile["required"])
    assert set(reconcile["properties"]["outbox"]["enum"]) == {"wake_outbox", "run_waiter_wakes"}
    assert schemas["dispatch_reset"]["properties"]["to"]["default"] == "proposed"
    assert schemas["dispatch_detach"]["required"] == ["task_id"]
    assert requests == []


def test_diagnostics_detach_agent_and_recovery_use_client_contract(
    tools_and_requests: tuple[DispatchTools, list[httpx.Request]],
) -> None:
    tools, requests = tools_and_requests
    assert tools.task_diagnostics("task-a") == {"fixture": True}
    assert requests[-1].url.path == "/tasks/task-a/diagnostics"
    assert tools.detach_agent("task-a", **FIELDS) == {"fixture": True}
    assert requests[-1].url.path == "/tasks/task-a/recovery/detach_agent"
    assert json.loads(requests[-1].content) == FIELDS
    for operation in ("reset_proposed", "reset_queued"):
        assert tools.recover_task("task-a", operation=operation, **FIELDS) == {"fixture": True}
        assert requests[-1].url.path == f"/tasks/task-a/recovery/{operation}"
        assert json.loads(requests[-1].content) == FIELDS
    assert all(
        r.headers["authorization"].endswith("control-fixture")
        for r in requests
        if r.method == "POST"
    )


def test_reset_defaults_and_target_detach_are_unchanged(
    tools_and_requests: tuple[DispatchTools, list[httpx.Request]],
) -> None:
    tools, requests = tools_and_requests
    tools.reset("task-a")
    tools.detach("task-a")
    assert [(r.method, r.url.path) for r in requests] == [
        ("POST", "/tasks/task-a/reset"),
        ("POST", "/tasks/task-a/detach"),
    ]
    requests.clear()
    for destination in ("proposed", "queued"):
        tools.reset("task-a", to=destination, **FIELDS)
        assert requests[-1].url.path == f"/tasks/task-a/recovery/reset_{destination}"
        assert json.loads(requests[-1].content) == FIELDS


@pytest.mark.parametrize(
    "fields",
    [
        {"to": "other"},
        {"to": "queued"},
        {"expected_revision": "revision-a"},
        {"to": "queued", **FIELDS, "reason": " "},
        {"to": "queued", **FIELDS, "expected_status": "queued"},
    ],
)
def test_invalid_reset_does_not_open_client(
    fields: dict,
    tools_and_requests: tuple[DispatchTools, list[httpx.Request]],
) -> None:
    tools, requests = tools_and_requests
    with pytest.raises(ValueError):
        tools.reset("task-a", **fields)
    assert requests == []


def test_reconcile_accepts_no_caller_outcome_or_send_identity(
    tools_and_requests: tuple[DispatchTools, list[httpx.Request]],
) -> None:
    tools, requests = tools_and_requests
    fields = {
        "outbox": "wake_outbox",
        "wake_id": "wake-a",
        "expected_revision": "revision-a",
        "actor": "test-operator",
        "reason": "observe",
    }
    tools.reconcile_wake("task-a", **fields)
    assert requests[-1].url.path == "/tasks/task-a/wakes/reconcile"
    assert json.loads(requests[-1].content) == fields
    count = len(requests)
    for name in ("outcome", "send_token"):
        with pytest.raises(TypeError):
            tools.reconcile_wake("task-a", **fields, **{name: "caller-label"})
    assert len(requests) == count


def test_old_provider_has_no_effect_from_mcp() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    tools = DispatchTools(
        client_factory=lambda: DispatchClient(
            "https://dispatch.invalid",
            transport=httpx.MockTransport(handle),
        )
    )
    with pytest.raises(DispatchUpgradeRequired, match="task_recovery/v1"):
        tools.detach_agent("task-a", **FIELDS)
    assert [(r.method, r.url.path) for r in requests] == [("GET", "/health")]
