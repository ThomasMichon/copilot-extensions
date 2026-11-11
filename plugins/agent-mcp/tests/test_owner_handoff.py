"""The CLI accepts only paired explicit owners and never replays a refusal."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_mcp import custody_launch, custody_lifetime, forward
from agent_mcp.__main__ import main


@pytest.fixture(autouse=True)
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("AGENT_MCP_NO_MULTIPLEX", "AGENT_MCP_NO_SERVE", "AGENT_MCP_NO_ENSURE_SERVE"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("command", ["bridge", "forward"])
def test_paired_cli_owner_reaches_adopted_entrypoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str,
) -> None:
    config = tmp_path / "bridge.json"
    config.write_text("{}", encoding="utf-8")
    socket = str(tmp_path / "serve.sock")
    monkeypatch.setenv("AGENT_MCP_SERVE_SOCKET", socket)
    adopted, ordinary = Mock(return_value=0), Mock()
    monkeypatch.setattr(custody_launch, "run_adopted", adopted)
    monkeypatch.setattr(forward, "run", ordinary)
    assert main([
        command, "--config", str(config), "--session-owner-pid", "42",
        "--session-owner-birth", "native:1",
    ]) == 0
    adopted.assert_called_once_with(
        socket, str(config.resolve()), owner_pid=42, owner_birth="native:1",
    )
    ordinary.assert_not_called()


@pytest.mark.parametrize("options", [
    ["--session-owner-pid", "42"],
    ["--session-owner-birth", "native:1"],
    ["--session-owner-pid", "0", "--session-owner-birth", "native:1"],
    ["--session-owner-pid", "42", "--session-owner-birth", ""],
])
def test_incomplete_or_invalid_owner_refuses_before_native_or_startup_effects(
    monkeypatch: pytest.MonkeyPatch, options: list[str], capsys: pytest.CaptureFixture,
) -> None:
    pin, ensure, ordinary = Mock(), Mock(), Mock()
    monkeypatch.setattr(custody_lifetime, "_pin", pin)
    monkeypatch.setattr(custody_launch.incoming, "ensure_current", ensure)
    monkeypatch.setattr(forward, "run", ordinary)
    assert main(["bridge", "echo", *options]) in (1, 2)
    pin.assert_not_called()
    ensure.assert_not_called()
    ordinary.assert_not_called()
    assert not capsys.readouterr().out


@pytest.mark.parametrize("key", [
    "AGENT_MCP_NO_MULTIPLEX", "AGENT_MCP_NO_SERVE", "AGENT_MCP_NO_ENSURE_SERVE",
])
def test_explicit_custody_preserves_startup_opt_out(
    monkeypatch: pytest.MonkeyPatch, key: str,
) -> None:
    monkeypatch.setenv(key, "1")
    adopted = Mock()
    monkeypatch.setattr(custody_launch, "run_adopted", adopted)
    assert main([
        "bridge", "echo", "--session-owner-pid", "42", "--session-owner-birth", "native:1",
    ]) == 2
    adopted.assert_not_called()


def test_owner_refusal_never_falls_back_to_another_session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(custody_lifetime, "_pin", Mock(side_effect=ProcessLookupError("changed owner")))
    ensure, ordinary = Mock(), Mock()
    monkeypatch.setattr(custody_launch.incoming, "ensure_current", ensure)
    monkeypatch.setattr(forward, "run", ordinary)
    assert main([
        "forward", "echo", "--socket", "explicit.sock",
        "--session-owner-pid", "42", "--session-owner-birth", "native:1",
    ]) == 1
    ensure.assert_not_called()
    ordinary.assert_not_called()
    assert "changed owner" in capsys.readouterr().err
