"""Portable context routing stays pure; real receiving processes are opt-in."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture
def receiver(monkeypatch):
    class Refused(ValueError):
        pass

    calls = []
    procutil = ModuleType("agent_procutil")
    procutil.no_window_flags = lambda: 0
    canonical = ModuleType("repository_policy")
    canonical.AgentHostingError = Refused
    canonical.RepositoryAgentPolicy = object
    canonical.require_agent_hosting = lambda *args, **kwargs: calls.append((args, kwargs))
    monkeypatch.setitem(sys.modules, "agent_procutil", procutil)
    monkeypatch.setitem(sys.modules, "repository_policy", canonical)
    receiving = (
        Path(__file__).resolve().parents[2] / "libs/repository-policy/src/repository_policy/receiving.py"
    )
    receiving_spec = importlib.util.spec_from_file_location("repository_policy.receiving", receiving)
    assert receiving_spec and receiving_spec.loader
    receiving_module = importlib.util.module_from_spec(receiving_spec)
    monkeypatch.setitem(sys.modules, "repository_policy.receiving", receiving_module)
    receiving_spec.loader.exec_module(receiving_module)
    source = (
        Path(__file__).resolve().parents[2] / "plugins/agent-bridge/src/agent_bridge"
        / "session_host/receiver_policy.py"
    )
    spec = importlib.util.spec_from_file_location("portable_receiver_unit", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls, Refused


@pytest.mark.parametrize("context", [
    "", "[]", "{}",
    '{"schema":"agent-bridge.receiving-policy","version":true,"marketplaceId":""}',
    '{"schema":"agent-bridge.receiving-policy","version":2,"marketplaceId":""}',
    '{"schema":"agent-bridge.receiving-policy","version":1,"marketplaceId":null}',
], ids=["empty", "array", "missing-fields", "boolean-version", "future-version", "null-marketplace"])
def test_invalid_context_refuses_before_canonical_read(receiver, context):
    module, calls, refused = receiver
    with pytest.raises(refused):
        module.require_portable_path_hosting("/actual", context)
    assert calls == []


def test_selected_context_and_actual_checkout_reach_the_canonical_owner(receiver, monkeypatch):
    module, calls, _refused = receiver
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", '{"marketplaceId":"unrelated"}')
    context = json.dumps({
        "schema": "agent-bridge.receiving-policy", "version": 1, "marketplaceId": "selected",
    })
    for operation in ("launch", "resume", "successor"):
        module.require_portable_path_hosting("/actual", context, operation=operation)
    assert calls == [
        (("/actual",), {"marketplace_id": "selected", "operation": operation})
        for operation in ("launch", "resume", "successor")
    ]
