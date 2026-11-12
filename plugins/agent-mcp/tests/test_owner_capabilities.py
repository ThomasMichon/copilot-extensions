"""Selected-runtime discovery is not serving admission or a payload-version hint."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from agent_procutil import _daemon_instance_identity

from agent_mcp import custody_lifetime, cutover, incoming, owner_capabilities
from agent_mcp.__main__ import main
from agent_mcp.owner_bootstrap import ENTRY_CAPABILITY, receiver


@pytest.fixture
def selected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple:
    layout = ("current-slot", tmp_path / "versions" / "current-slot", tmp_path)
    monkeypatch.setattr(incoming, "require_selected", Mock(return_value=layout))
    monkeypatch.setattr(cutover, "_require_selected", Mock())
    monkeypatch.setattr(owner_capabilities, "_platform", lambda: "linux")
    monkeypatch.setattr(_daemon_instance_identity, "birth_identity", lambda _pid: "native:1")
    monkeypatch.setattr(custody_lifetime, "_pin", lambda *_args: (lambda: True, lambda: None))
    monkeypatch.setattr(
        incoming, "ensure_current",
        Mock(side_effect=AssertionError("discovery cannot start serving")),
    )
    return layout


def test_unmanaged_runtime_cannot_advertise_installed_support(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(incoming, "require_selected", lambda: None)
    pin = Mock(side_effect=AssertionError("unmanaged discovery cannot acquire a native owner"))
    monkeypatch.setattr(custody_lifetime, "_pin", pin)
    assert main(["session-owner-capabilities"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["supported"] is False
    assert result["reason"] == "unmanaged-runtime"
    assert "selection" not in result
    pin.assert_not_called()


def test_selected_executable_support_is_bound_before_and_after_native_probe(
    monkeypatch: pytest.MonkeyPatch, selected: tuple, capsys: pytest.CaptureFixture,
) -> None:
    events = []
    monkeypatch.setattr(
        incoming, "require_selected",
        lambda: events.append("physical-selection") or selected,
    )
    def released() -> None:
        events.append("native-released")
    monkeypatch.setattr(
        custody_lifetime, "_pin",
        lambda *_args: (lambda: True, released),
    )
    monkeypatch.setattr(
        cutover, "_require_selected",
        lambda layout: events.append(("rechecked", layout)),
    )
    assert main(["session-owner-capabilities"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["supported"] is True
    assert result["selection"]["generation"] == selected[0]
    assert result["selection"]["root"] == str(selected[2].resolve())
    assert events == ["physical-selection", ("rechecked", selected), "native-released"]


def test_changed_selection_is_a_failure_not_supported_or_ordinary_readiness(
    monkeypatch: pytest.MonkeyPatch, selected: tuple, capsys: pytest.CaptureFixture,
) -> None:
    released = Mock()
    monkeypatch.setattr(custody_lifetime, "_pin", lambda *_args: (lambda: True, released))
    monkeypatch.setattr(
        cutover, "_require_selected",
        Mock(side_effect=incoming.AdmissionRefused("selection changed")),
    )
    assert main(["session-owner-capabilities"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["supported"] is False
    assert "selection changed" in result["error"]
    assert "selection" not in result
    released.assert_called_once_with()


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_optional_receiver_is_advertised_only_on_its_supported_platform(
    monkeypatch: pytest.MonkeyPatch, selected: tuple, capsys: pytest.CaptureFixture,
    platform: str,
) -> None:
    monkeypatch.setattr(owner_capabilities, "_platform", lambda: platform)
    assert main(["session-owner-capabilities"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["supported"] is True
    assert result["platform"] == platform
    capabilities = result["contract"]["capabilities"]
    assert "explicit-native-owner-pair" in capabilities
    assert (ENTRY_CAPABILITY in capabilities) is (platform == "linux")
    result["entry"]["module"] = str(
        Path(result["entry"]["prefix"]) / "lib" / "agent_mcp" / "owner_entry.py",
    )
    assert (receiver(result) is not None) is (platform == "linux")
    incoming.ensure_current.assert_not_called()


def test_unavailable_native_probe_never_advertises_support(
    monkeypatch: pytest.MonkeyPatch, selected: tuple, capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(
        custody_lifetime, "_pin", Mock(side_effect=OSError("native pin unavailable")),
    )
    assert main(["session-owner-capabilities"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["supported"] is False
    assert "native pin unavailable" in result["error"]
    assert "selection" not in result
    cutover._require_selected.assert_not_called()


def test_unsupported_platform_does_not_probe_or_claim_native_support(
    monkeypatch: pytest.MonkeyPatch, selected: tuple, capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(owner_capabilities, "_platform", lambda: None)
    pin = Mock()
    monkeypatch.setattr(custody_lifetime, "_pin", pin)
    assert main(["session-owner-capabilities"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["supported"] is False
    assert result["reason"] == "unsupported-platform"
    pin.assert_not_called()


@pytest.mark.parametrize("key", [
    "AGENT_MCP_NO_MULTIPLEX", "AGENT_MCP_NO_SERVE", "AGENT_MCP_NO_ENSURE_SERVE",
])
def test_supported_contract_does_not_override_current_startup_opt_out(
    monkeypatch: pytest.MonkeyPatch, selected: tuple, key: str, capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setenv(key, "1")
    assert main(["session-owner-capabilities"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["supported"] is True
    assert result["invocation_allowed"] is False


def test_unselected_executable_refuses_before_native_probe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(
        incoming, "require_selected",
        Mock(side_effect=incoming.AdmissionRefused("physical slot is no longer selected")),
    )
    pin = Mock()
    monkeypatch.setattr(custody_lifetime, "_pin", pin)
    assert main(["session-owner-capabilities"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["supported"] is False
    assert "no longer selected" in result["error"]
    pin.assert_not_called()
