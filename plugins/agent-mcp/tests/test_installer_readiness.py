from __future__ import annotations

import json

from agent_mcp import __main__ as cli
from agent_mcp import config
from agent_mcp.config import ConfigError
from agent_mcp.installer_readiness import emit, evaluate


def test_no_bridges_is_configuration_empty(capsys):
    result = evaluate([])

    assert result["state"] == "configuration-empty"
    assert emit(result) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_configured_bridges_are_validated_without_starting_them(tmp_path):
    bridge = tmp_path / "demo.yaml"
    bridge.write_text("fixture\n", encoding="utf-8")
    loaded = []

    result = evaluate([bridge], loader=lambda path: loaded.append(path))

    assert result["state"] == "ready"
    assert loaded == [str(bridge.resolve())]


def test_invalid_bridge_is_failed_not_empty(capsys, tmp_path):
    bridge = tmp_path / "broken.yaml"
    bridge.write_text("fixture\n", encoding="utf-8")

    def fail(_path: str):
        raise ConfigError("missing server")

    result = evaluate([bridge], loader=fail)

    assert result["state"] == "failed"
    assert "agent-mcp validate" in result["detail"]
    assert emit(result) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"


def test_payload_command_does_not_create_bridge_configuration(
    monkeypatch, capsys, tmp_path
):
    bridges = tmp_path / "bridges"
    monkeypatch.setattr(config, "BRIDGES_DIR", bridges)
    monkeypatch.setattr(config, "discover_plugin_bridges", lambda: {})

    assert cli.main(["installer-readiness"]) == 0
    assert not bridges.exists()
    assert json.loads(capsys.readouterr().out)["state"] == "configuration-empty"
