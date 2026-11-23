"""Actor-free capability, routing and uncertainty controls for explicit unlock."""

from __future__ import annotations

import importlib
import ast
import math
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "promotion_vault_unlock_selection"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "plugins/agent-vault/src/agent_vault")]
sys.modules[PACKAGE] = package
caller = importlib.import_module(f"{PACKAGE}.unlock_client")
qualification = importlib.import_module(f"{PACKAGE}.cache_warm_client")
transport = importlib.import_module(f"{PACKAGE}.command_transport")
selection = importlib.import_module(f"{PACKAGE}.cache_warm_selection")


def test_core_retained_sender_pins_resolved_endpoint_token_and_timeout() -> None:
    source = ROOT / "plugins/agent-vault/src/agent_vault/core_ext.py"
    tree = ast.parse(source.read_bytes(), filename=str(source))
    body: list[ast.stmt] = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.FunctionDef) and node.name in ("_core_timeout", "bind_core_transport")
    ]
    assert len(body) == 3
    environment = {"endpoint": "tcp:127.0.0.1:19071", "token": "fixture-original", "timeout": "12"}
    original, replacement = SimpleNamespace(), SimpleNamespace()
    original.usable = lambda _accept: original
    replacement.usable = lambda _accept: replacement
    endpoints = [original, replacement]
    discoveries: list[str] = []
    calls: list[tuple[object, dict[str, Any], float | None]] = []

    def discover(_directory: Path, *, override: str, probe: object) -> Any:
        discoveries.append(override)
        return endpoints[0] if override.endswith("19071") else endpoints[1]

    def dial(endpoint: object, payload: dict[str, Any], timeout: float | None) -> dict[str, Any]:
        calls.append((endpoint, payload, timeout))
        return {"ok": True}

    class EndpointUnavailable(RuntimeError):
        pass

    def resolve_bound(
        _directory: Path, *, override: object, legacy: object, probe: object,
    ) -> object:
        assert override is original and legacy is None and probe is None
        return override

    delegate_source = ROOT / "plugins/agent-vault/src/agent_vault/coredelegation.py"
    delegate_tree = ast.parse(delegate_source.read_bytes(), filename=str(delegate_source))
    delegate_body: list[ast.stmt] = [
        node for node in delegate_tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.FunctionDef) and node.name == "delegate"
    ]
    assert len(delegate_body) == 2
    delegate_namespace: dict[str, Any] = {
        "Path": Path, "DEFAULT_TIMEOUT": 5.0, "connect_probe": object(),
        "EndpointUnavailable": EndpointUnavailable, "resolve": resolve_bound,
        "default_accept": lambda _transport: True, "_dial": dial, "TOKEN_KEY": "_token",
    }
    exec(
        compile(ast.Module(body=delegate_body, type_ignores=[]), str(delegate_source), "exec"),
        delegate_namespace,
    )
    namespace: dict[str, Any] = {
        "os": SimpleNamespace(environ=environment), "math": math,
        "CORE_ENDPOINT_ENV": "endpoint", "CORE_TOKEN_ENV": "token", "CORE_TIMEOUT_ENV": "timeout",
        "DEFAULT_CORE_TIMEOUT": 30.0, "core_runtime_dir": lambda: ROOT,
        "resolve": discover, "connect_probe": delegate_namespace["connect_probe"],
        "default_accept": delegate_namespace["default_accept"],
        "EndpointUnavailable": EndpointUnavailable, "delegate": delegate_namespace["delegate"],
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(source), "exec"), namespace)
    sender = namespace["bind_core_transport"](SimpleNamespace())
    assert sender is not None
    assert sender({"action": "ping"}, None, SimpleNamespace())["ok"]
    environment.update(endpoint="tcp:127.0.0.1:19072", token="fixture-replacement", timeout="99")
    assert sender({"action": "unlock", "password": "fixture-master"}, 20.0, SimpleNamespace())["ok"]
    assert discoveries == ["tcp:127.0.0.1:19071"]
    assert [endpoint for endpoint, _, _ in calls] == [endpoints[0], endpoints[0]]
    assert [payload["_token"] for _, payload, _ in calls] == ["fixture-original"] * 2
    assert [timeout for _, _, timeout in calls] == [12.0, 12.0]
    assert calls[-1][1]["password"] == "fixture-master"


def test_unlock_capability_requires_a_ready_actual_peer_and_integer_generation() -> None:
    for versions in (None, [], [True], ["1"], [2]):
        assert not qualification.supported({
            "ok": True, "accepting_work": True, "version": "9999",
            "cache_warm_selection_versions": versions,
        })
    assert not qualification.supported({
        "ok": True, "accepting_work": False, "cache_warm_selection_versions": [1],
    })
    assert qualification.supported({
        "ok": True, "accepting_work": True, "cache_warm_selection_versions": [1, 2],
    })


def test_unlock_sender_keeps_context_and_only_projects_negotiated_selection() -> None:
    calls: list[dict[str, Any]] = []
    context, route = SimpleNamespace(), transport.CommandRoute()
    selected = selection.WarmSelection(str(Path(ROOT.anchor) / "selected"), True)

    def send(request: dict[str, Any], _timeout: float | None, **options: Any) -> dict[str, Any]:
        assert options == {"_context": context, "_route": route}
        calls.append(request)
        return {"ok": True}

    request = {"action": "unlock", "password": "fixture-master"}
    for capable in (False, True):
        session = caller.UnlockSession(send, context, route, selected, capable)
        assert session.send(request)["ok"]
        assert (selection.FIELD in calls[-1]) is capable
        if capable:
            assert selection.decode(calls[-1][selection.FIELD]) == selected
    assert selection.FIELD not in request
    assert len(calls) == 2


def test_uncertain_unlock_calls_its_retained_sender_once_without_replay() -> None:
    outcomes: tuple[object, ...] = (
        None, [], {}, {"needs_unlock": True}, {"ok": None}, {"ok": 0}, {"ok": "false"},
        OSError("private transport diagnostic"),
    )
    for outcome in outcomes:
        calls: list[str] = []

        def send(request: dict[str, Any], _timeout: float | None, **_options: Any) -> Any:
            calls.append(request["action"])
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        session = caller.UnlockSession(
            send, SimpleNamespace(), transport.CommandRoute(),
            selection.WarmSelection(str(Path(ROOT.anchor) / "selected"), False), True,
        )
        with pytest.raises(caller.UnlockOutcomeUnknown):
            session.send({"action": "unlock"})
        assert calls == ["unlock"]


def test_unlock_scope_does_not_capture_health_explicit_routes_or_later_requests() -> None:
    calls: list[str] = []

    class NativeBoundary(RuntimeError):
        pass

    def native(*_args: Any, **_kwargs: Any) -> Any:
        raise NativeBoundary

    def selected(request: dict[str, Any], _timeout: float | None) -> dict[str, Any]:
        calls.append(request["action"])
        return {"ok": True}

    boundary = SimpleNamespace(
        resolve_context=native, host="127.0.0.1", discover=native,
        unavailable=NativeBoundary, invalid=NativeBoundary,
    )
    with transport.use_unlock_sender(selected):
        assert transport.send_command(boundary, {"action": "unlock"})["ok"]
        with pytest.raises(NativeBoundary):
            transport.send_command(boundary, {"action": "ping"})
        with pytest.raises(NativeBoundary):
            transport.send_command(
                boundary, {"action": "unlock"}, _context=SimpleNamespace(port=19371),
            )
        route = transport.CommandRoute(sender=lambda *_args: {"ok": True, "retained": True})
        assert transport.send_command(boundary, {"action": "unlock"}, _route=route)["retained"]
    with pytest.raises(NativeBoundary):
        transport.send_command(boundary, {"action": "unlock"})
    assert calls == ["unlock"]


def test_readonly_status_never_reuses_old_or_rolled_back_resident_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(package, "cli", SimpleNamespace(
        _ready_for_work=lambda reply: isinstance(reply, dict) and reply.get("ok") is True,
    ), raising=False)
    for mode in (
        "current", "old", "rollback", "lost", "dropped", "boolean-receipt", "transition",
    ):
        calls: list[dict[str, Any]] = []
        health: dict[str, Any] = {
            "ok": True, "accepting_work": True,
            "cache_warm_selection_versions": [] if mode == "old" else [1],
            "cache_warm": selection.WarmState("complete", selected=1, cached=1).describe(),
        }
        response: dict[str, Any] = {
            **health, "cache_warm_selection_versions": [] if mode == "rollback" else [1],
            "accepting_work": mode != "transition",
            "cache_warm": {
                **health["cache_warm"],
                **({selection.RECEIPT_FIELD: True if mode == "boolean-receipt" else 1}
                   if mode != "dropped" else {}),
            },
        }
        chosen = selection.WarmSelection(str(Path(ROOT.anchor) / "selected"), False)

        def send(request: dict[str, Any], _timeout: float | None, **_options: Any) -> Any:
            calls.append(request)
            assert request["action"] == "ping"
            assert selection.decode(request[selection.FIELD]) == chosen
            return None if mode == "lost" else response

        selected = qualification.SelectedService(
            send, SimpleNamespace(), transport.CommandRoute(), chosen, health, mode != "old",
        )

        @contextmanager
        def selected_service() -> Iterator[Any]:
            yield selected

        monkeypatch.setattr(qualification, "selected_service", selected_service)
        actual = qualification.scoped_status()
        if mode == "lost":
            assert actual is None
        else:
            assert actual["cache_warm"] == (response["cache_warm"] if mode == "current" else None)
            assert actual is not health and actual is not response
        assert health["cache_warm"]["state"] == "complete"
        assert len(calls) == (0 if mode == "old" else 1)
