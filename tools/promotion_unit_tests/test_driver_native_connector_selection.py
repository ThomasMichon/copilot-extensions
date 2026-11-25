"""Launch-scoped connector selection with storage and execution replaced."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
RECEIVER = "plugins/agent-bridge/src/agent_bridge/receiving_mux.py"
STAGING = "libs/copilot-driver-core/src/agent_remote_driver/headed/staging.py"
CONTRACT = "driver-native-v1"
SESSION = "11111111-1111-4111-8111-111111111111"
ATTEMPT = "22222222-2222-4222-8222-222222222222"


def _owner(relative: str) -> SimpleNamespace:
    return SimpleNamespace(**runpy.run_path(str(ROOT / relative)))


def _selection() -> tuple[dict[str, Any], dict[str, str]]:
    selected: dict[str, Any] = {
        "contract": CONTRACT, "version": 1, "sessionId": SESSION, "cwd": str(ROOT),
        "scopeId": "fixture-scope", "attempt": ATTEMPT,
        "bridgePluginRoot": str(ROOT / "bridge"), "driverPluginRoot": str(ROOT / "driver"),
    }
    env: dict[str, str] = {
        "AGENT_BRIDGE_NATIVE_SESSION_ID": SESSION,
        "AGENT_BRIDGE_SCOPE_ID": selected["scopeId"], "AGENT_BRIDGE_PORTABLE_ATTEMPT": ATTEMPT,
        "AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT": selected["bridgePluginRoot"],
        "AGENT_BRIDGE_REMOTE_DRIVER_ROOT": selected["driverPluginRoot"],
    }
    return selected, env


def test_original_headed_selection_is_legacy_without_a_scoped_declaration() -> None:
    owner = _owner(STAGING)
    assert owner.connector_selection({}, session_id=SESSION, cwd=str(ROOT)) is None
    selected, env = _selection()
    env[owner.CONNECTOR_ENV] = json.dumps(selected)
    assert owner.connector_selection(env, session_id=SESSION, cwd=str(ROOT)) == selected


@pytest.mark.parametrize("field,value", [
    ("contract", "unknown"), ("version", True), ("sessionId", "other"),
    ("cwd", "other"), ("scopeId", "other"), ("attempt", "other"),
    ("bridgePluginRoot", "relative"), ("driverPluginRoot", "relative"),
    ("connectorPluginRoot", "caller-cannot-select-the-staged-owner"),
], ids=["contract", "version", "session", "cwd", "scope", "attempt", "bridge", "driver", "extra"])
def test_headed_selection_refuses_rebinding_before_native_birth(field: str, value: object) -> None:
    owner = _owner(STAGING)
    selected, env = _selection()
    env[owner.CONNECTOR_ENV] = json.dumps({**selected, field: value})
    with pytest.raises(ValueError, match="explicit receiving launch"):
        owner.connector_selection(env, session_id=SESSION, cwd=str(ROOT))


class _File:
    def __init__(self, path: Path, storage: dict[str, bytes]):
        self.path, self.storage = path, storage

    def __truediv__(self, name: str) -> _File:
        return _File(self.path / name, self.storage)

    def __str__(self) -> str:
        return str(self.path)

    @property
    def parent(self) -> _File:
        return _File(self.path.parent, self.storage)

    def exists(self) -> bool:
        return str(self) in self.storage

    def is_symlink(self) -> bool:
        return False

    def read_bytes(self) -> bytes:
        return self.storage[str(self)]

    def read_text(self, *, encoding: str) -> str:
        return self.read_bytes().decode(encoding)


def _receiving(
    monkeypatch: pytest.MonkeyPatch, *, packet: dict[str, Any] | None = None,
) -> tuple[SimpleNamespace, dict[str, Any], _File, dict[str, bytes]]:
    owner = _owner(RECEIVER)
    storage: dict[str, bytes] = {}
    support = _File(ROOT / "fixture" / "support", storage)
    storage[str(support / "driver.pyz")] = b"original retained Core"
    storage[str(support / "support.json")] = json.dumps(
        {"native_connectors": [CONTRACT]} if packet is None else packet,
    ).encode()
    request = {
        "version": 1, "attempt": ATTEMPT, "session_id": SESSION, "cwd": str(ROOT),
        "state_root": str(ROOT / "fixture-state"), "scope_id": "fixture-scope",
        "policy_context": "fixture-policy", "support_sha256": "c" * 64, "copilot_args": [],
        "prefer_native_connector": True,
    }
    monkeypatch.setitem(
        owner._retain_connector.__globals__, "_write",
        lambda path, value: storage.__setitem__(str(path), json.dumps(value).encode()),
    )
    return owner, request, support, storage


@pytest.mark.parametrize("offered,included,preferred,expected", [
    ([], [CONTRACT], True, False),
    ([CONTRACT], [], True, False),
    ([CONTRACT], [CONTRACT], False, False),
    ([CONTRACT], [CONTRACT], True, True),
], ids=["provider-rollback", "old-payload", "unchanged-default", "selected-current"])
def test_receiving_selects_only_mutually_supported_preferred_composition(
    monkeypatch: pytest.MonkeyPatch, offered: list[str], included: list[str], preferred: bool, expected: bool,
) -> None:
    owner, request, support, storage = _receiving(
        monkeypatch, packet={"native_connectors": included},
    )
    request["prefer_native_connector"] = preferred
    owner._retain_connector(request, support, {"headed_connectors": offered})
    assert owner._selected_connector(request, support) is expected
    retained = storage[str(support.parent / "connector.json")]
    owner._retain_connector(request, support, {"headed_connectors": offered})
    assert storage[str(support.parent / "connector.json")] == retained
    if expected:
        with pytest.raises(ValueError, match="selection changed"):
            owner._retain_connector(request, support, {})
        assert storage[str(support.parent / "connector.json")] == retained


@pytest.mark.parametrize("advertisement", [None, True, 1, CONTRACT, {}],
                         ids=["null", "boolean", "number", "string", "object"])
def test_malformed_connector_capability_never_selects_or_executes(
    monkeypatch: pytest.MonkeyPatch, advertisement: object,
) -> None:
    owner, request, support, storage = _receiving(monkeypatch)
    with pytest.raises(ValueError, match="connector capability"):
        owner._retain_connector(request, support, {"headed_connectors": advertisement})
    assert str(support.parent / "connector.json") not in storage


def test_retained_connector_refuses_replaced_core_without_resetting_its_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, request, support, storage = _receiving(monkeypatch)
    assert owner._selected_connector({**request, "prefer_native_connector": False}, support) is False
    with pytest.raises(ValueError, match="selection is unavailable"):
        owner._selected_connector(request, support)
    owner._retain_connector(request, support, {"headed_connectors": [CONTRACT]})
    original = storage[str(support.parent / "connector.json")]
    storage[str(support / "driver.pyz")] = b"replacement must not inherit the original attempt"
    with pytest.raises(ValueError, match="selected payload"):
        owner._selected_connector(request, support)
    assert storage[str(support.parent / "connector.json")] == original


@pytest.mark.parametrize("selected", [False, True])
def test_actual_child_dispatch_clears_inherited_selection_and_uses_only_its_retained_choice(
    monkeypatch: pytest.MonkeyPatch, selected: bool,
) -> None:
    owner, request, support, storage = _receiving(monkeypatch)
    request["cwd"] = str(Path.cwd().resolve())
    owner._retain_connector(request, support, {"headed_connectors": [CONTRACT] if selected else []})
    storage[str(support.parent / "launch.json")] = json.dumps(request).encode()
    effects = []
    scope = owner.child.__globals__
    monkeypatch.setitem(scope, "_directory", lambda _path: None)
    monkeypatch.setitem(scope, "_policy", lambda *_args: None)
    monkeypatch.setitem(scope, "_core_executable", lambda _path: ["python", "-I", "-S", "retained.pyz"])
    monkeypatch.setitem(scope, "os", SimpleNamespace(
        environ={"COPILOT_DRIVER_NATIVE_CONNECTOR": "foreign-parent", "SESSION_ID": "parent"},
        execvpe=lambda *args: effects.append(args),
    ))
    owner.child(support.parent)
    assert len(effects) == 1
    _binary, argv, env = effects[0]
    assert "SESSION_ID" not in env
    assert argv[-4:] == ["--plugin-dir", str(support / "plugins" / "agent-remote-driver"),
                         "--plugin-dir", str(support / "plugins" / "agent-bridge")]
    if selected:
        declaration = json.loads(env["COPILOT_DRIVER_NATIVE_CONNECTOR"])
        assert declaration["sessionId"] == SESSION and declaration["cwd"] == request["cwd"]
        assert declaration["scopeId"] == request["scope_id"] and declaration["attempt"] == ATTEMPT
        assert declaration["bridgePluginRoot"] == env["AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT"]
        assert "connectorPluginRoot" not in declaration
    else:
        assert "COPILOT_DRIVER_NATIVE_CONNECTOR" not in env
