"""HTTP MCP recovery bindings stay on owning services and control authority."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("mcp", reason="requires the optional 'mcp' extra")

from agent_dispatch.events import EventBus
from agent_dispatch.mcp_http import build_coordinator_mcp
from agent_dispatch.mcp_task_recovery_http import register_task_recovery_tools

FIELDS = {
    "expected_revision": "revision-a",
    "request_id": "request-a",
    "actor": "test-operator",
    "reason": "synthetic recovery",
}


class Registry:
    def __init__(self) -> None:
        self.functions: dict[str, Callable[..., dict]] = {}

    def tool(self, *, name: str) -> Callable:
        def register(function: Callable[..., dict]) -> Callable[..., dict]:
            self.functions[name] = function
            return function

        return register


def test_actual_http_mcp_registration_matches_stdio_names_and_fields() -> None:
    server = build_coordinator_mcp(SimpleNamespace(), EventBus(), control_token="control-fixture")
    registered = asyncio.run(server.list_tools())
    schemas = {tool.name: tool.input_schema for tool in registered}
    assert {
        "dispatch_task_diagnostics",
        "dispatch_recover_task",
        "dispatch_detach_agent",
        "dispatch_reset",
        "dispatch_reconcile_wake",
        "dispatch_detach",
    } <= schemas.keys()
    assert set(schemas["dispatch_recover_task"]["required"]) == {"task_id", "operation", *FIELDS}
    assert "ctx" not in schemas["dispatch_recover_task"]["properties"]
    assert schemas["dispatch_reset"]["properties"]["to"]["default"] == "proposed"
    assert set(schemas["dispatch_reconcile_wake"]["properties"]) == {
        "task_id",
        "outbox",
        "wake_id",
        "expected_revision",
        "actor",
        "reason",
    }


@pytest.fixture
def bindings() -> tuple[dict[str, Callable], list, list, object]:
    calls: list = []
    events: list = []
    context = object()

    def diagnostics(task_id: str) -> dict:
        calls.append(("diagnostics", task_id))
        return {
            "contract": "task_recovery/v1",
            "task": {"status": "queued"},
            "revision": "revision-a",
        }

    def recover(task_id: str, **fields: Any) -> dict:
        calls.append(("recover", task_id, fields))
        return {
            "operation": fields["operation"],
            "request_id": fields["request_id"],
            "replayed": False,
            "task": {"status": "proposed"},
        }

    def legacy_reset(task_id: str, **fields: Any) -> dict:
        calls.append(("legacy_reset", task_id, fields))
        return {"status": "proposed"}

    def control_error(ctx: object) -> dict | None:
        if ctx is context:
            return None
        return {"reason": "invalid_control_authority"}

    registry = Registry()
    register_task_recovery_tools(
        registry,
        SimpleNamespace(task_diagnostics=diagnostics, recover_task=recover),
        SimpleNamespace(publish=events.append),
        control_error=control_error,
        legacy_reset=legacy_reset,
    )
    return registry.functions, calls, events, context


def test_http_recovery_requires_control_before_calling_owner(bindings: tuple) -> None:
    tools, calls, events, _ = bindings
    result = tools["dispatch_detach_agent"](object(), "task-a", **FIELDS)
    assert result["error"]["code"] == "task_recovery_forbidden"
    assert calls == events == []


def test_http_diagnostics_and_agent_detach_use_owning_methods(bindings: tuple) -> None:
    tools, calls, events, context = bindings
    result = tools["dispatch_task_diagnostics"]("task-a")
    assert result["revision"] == "revision-a"
    tools["dispatch_detach_agent"](context, "task-a", **FIELDS)
    assert calls == [
        ("diagnostics", "task-a"),
        ("recover", "task-a", {"operation": "detach_agent", **FIELDS}),
    ]
    assert len(events) == 1
    assert events[0]["type"] == "task.recovered"


def test_http_reset_keeps_default_and_strict_paths_distinct(bindings: tuple) -> None:
    tools, calls, _, context = bindings
    tools["dispatch_reset"](object(), "task-a")
    assert calls == [("legacy_reset", "task-a", {"reason": None, "expected_status": None})]
    for destination in ("proposed", "queued"):
        tools["dispatch_reset"](context, "task-a", to=destination, **FIELDS)
        assert calls[-1] == (
            "recover",
            "task-a",
            {"operation": f"reset_{destination}", **FIELDS},
        )
    count = len(calls)
    with pytest.raises(ValueError):
        tools["dispatch_reset"](context, "task-a", to="queued")
    assert len(calls) == count


def test_http_reconciliation_reuses_wake_owner_without_caller_outcomes(
    bindings: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import task_wake_recovery

    tools, calls, events, context = bindings
    result = {"outcome": "sent", "replayed": False, "receipt": {"send_id": "send-a"}}
    observed = []

    def observe(queue: object, task_id: str, **fields: Any) -> dict:
        observed.append((task_id, fields))
        return result

    monkeypatch.setattr(task_wake_recovery, "reconcile_wake", observe)
    fields = {
        "outbox": "wake_outbox",
        "wake_id": "wake-a",
        "expected_revision": "revision-a",
        "actor": "test-operator",
        "reason": "observe",
    }
    assert tools["dispatch_reconcile_wake"](object(), "task-a", **fields)["error"]
    assert observed == []
    assert tools["dispatch_reconcile_wake"](context, "task-a", **fields) is result
    assert observed == [("task-a", fields)]
    assert calls == []
    assert events == [{"type": "task.wake_reconciled", **result}]
    for name in ("outcome", "send_token"):
        with pytest.raises(TypeError):
            tools["dispatch_reconcile_wake"](context, "task-a", **fields, **{name: "caller-label"})
    assert len(observed) == 1
