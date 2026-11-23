"""Actor-free capability, routing and uncertainty controls for explicit unlock."""

from __future__ import annotations

import importlib
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
    outcomes: tuple[object, ...] = (None, [], OSError("private transport diagnostic"))
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
    for mode in ("current", "old", "rollback", "lost", "dropped", "boolean-receipt"):
        calls: list[dict[str, Any]] = []
        health: dict[str, Any] = {
            "ok": True, "accepting_work": True,
            "cache_warm_selection_versions": [] if mode == "old" else [1],
            "cache_warm": selection.WarmState("complete", selected=1, cached=1).describe(),
        }
        response: dict[str, Any] = {
            **health, "cache_warm_selection_versions": [] if mode == "rollback" else [1],
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
