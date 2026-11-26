"""Pure exact-stop evidence and opaque admission-fence decisions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def helper(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    package = ModuleType("_stop_evidence")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, package.__name__, package)
    path = (
        Path(__file__).resolve().parents[2]
        / "plugins/agent-bridge/src/agent_bridge/managed_stop_state.py"
    )
    spec = importlib.util.spec_from_file_location("_stop_evidence.managed_stop_state", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def client() -> Any:
    return SimpleNamespace(
        _on_event=None, _connection=object(), _prompt_complete=True,
        _prompt_in_flight=False, _prompt_error=None, _out_of_turn_open=False,
    )


@pytest.mark.parametrize("state", [
    "fresh", "missing", "disconnected", "error", "incomplete", "out-of-turn", "callback-changed",
])
def test_native_quiet_requires_current_observed_completion(helper: ModuleType, state: str) -> None:
    current = client()
    if state != "fresh":
        helper.begin_prompt(current)
        helper.record_completion(current)
        assert helper.settlement_available(current)
    if state == "missing":
        current = None
    elif state == "disconnected":
        current._connection = None
    elif state == "error":
        current._prompt_error = ""
    elif state == "incomplete":
        current._prompt_complete = False
    elif state == "out-of-turn":
        current._out_of_turn_open = True
    elif state == "callback-changed":
        current._on_event = lambda name, data: None
    assert not helper.settlement_available(current)


def test_active_settlement_requires_attached_connection(helper: ModuleType) -> None:
    current = client()
    current._prompt_in_flight = True
    assert helper.settlement_available(current)
    current._connection = None
    assert not helper.settlement_available(current)


def test_native_observer_invalidates_and_forwards_once(helper: ModuleType) -> None:
    current = client()
    forwarded: list[tuple[str, dict[str, Any]]] = []
    current._on_event = lambda name, data: forwarded.append((name, data))
    helper.begin_prompt(current)
    callback = current._on_event
    helper.record_completion(current)
    assert helper.settlement_available(current)
    helper.begin_prompt(current)
    assert current._on_event is callback and not helper.settlement_available(current)
    helper.record_completion(current)
    callback("session_state_changed", {"status": "running"})
    callback("session_state_changed", {"status": "idle"})
    assert not helper.settlement_available(current)
    assert forwarded == [
        ("session_state_changed", {"status": "running"}),
        ("session_state_changed", {"status": "idle"}),
    ]


@pytest.mark.parametrize("change", ["error", "in-flight", "out-of-turn", "connection", "callback"])
def test_record_does_not_promote_unconfirmed_client(helper: ModuleType, change: str) -> None:
    current = client()
    helper.begin_prompt(current)
    if change == "error":
        current._prompt_error = ""
    elif change == "in-flight":
        current._prompt_in_flight = True
    elif change == "out-of-turn":
        current._out_of_turn_open = True
    elif change == "connection":
        current._connection = None
    else:
        current._on_event = None
    helper.record_completion(current)
    current._prompt_in_flight = False
    assert not helper.settlement_available(current)


@pytest.mark.parametrize("token,manual,fenced", [
    (None, None, False), ("legacy-opaque-token", None, False),
    ("managed-exact-stop:1:original", None, True),
    ("managed-exact-stop:future:original", None, True),
    ("managed-exact-stop:", None, True),
    ("managed-exact-stop:1:original", "managed-exact-stop:1:other", True),
    ("managed-exact-stop:1:original", "managed-exact-stop:1:original", False),
])
def test_only_current_manual_admission_can_cross_exact_intent(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch,
    token: str | None, manual: str | None, fenced: bool,
) -> None:
    row = {"cold_recovery_stop_token": token}
    manager = SimpleNamespace(db=SimpleNamespace(get_session=lambda sid: row))
    monkeypatch.setitem(sys.modules, "_stop_evidence.cold_outages", SimpleNamespace(
        manual_recovery_stop_token=lambda owner, sid: manual,
    ))
    assert helper.prompt_fenced(manager, "original") is fenced


@pytest.mark.parametrize("token,admitted", [
    (None, False), ("legacy-opaque-token", False), ("managed-exact-stop:1:original", True),
])
def test_wind_down_override_requires_captured_exact_manual_intent(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch,
    token: str | None, admitted: bool,
) -> None:
    monkeypatch.setitem(sys.modules, "_stop_evidence.cold_outages", SimpleNamespace(
        manual_recovery_stop_token=lambda owner, sid: token,
    ))
    assert helper.manual_resume_admitted(SimpleNamespace(), "original", token) is admitted


@pytest.mark.parametrize("status,detail,refused", [
    (409, "exact_stop_requires_resume: explicit resume required", True),
    (409, {"code": "exact_stop_requires_resume: explicit resume required"}, True),
    (502, "exact_stop_requires_resume: explicit resume required", False),
    (409, "unrelated transport uncertainty", False),
])
def test_send_preserves_exact_intent_refusal_without_claiming_unknown_admission(
    monkeypatch: pytest.MonkeyPatch, status: int, detail: Any, refused: bool,
) -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "plugins/agent-bridge/src/agent_bridge/bridge_send_cli.py"
    )
    spec = importlib.util.spec_from_file_location("_intent_send", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    cause = RuntimeError("HTTP refusal")
    setattr(cause, "status", status)
    setattr(cause, "detail", detail)
    wrapped = RuntimeError("prompt admission")
    wrapped.__cause__ = cause
    assert module._proven_managed_refusal(wrapped, "managed") is refused
