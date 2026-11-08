from __future__ import annotations

import json

from agent_mcp import __main__ as cli
from agent_mcp import config
from agent_mcp.config import ConfigError, normalize_bridge_name
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

    result = evaluate([("demo", bridge)], loader=lambda path: loaded.append(path))

    assert result["state"] == "ready"
    assert loaded == [str(bridge.resolve())]


def test_invalid_bridge_is_failed_not_empty(capsys, tmp_path):
    bridge = tmp_path / "broken.yaml"
    bridge.write_text("fixture\n", encoding="utf-8")

    def fail(_path: str):
        raise ConfigError("missing server")

    result = evaluate([("broken", bridge)], loader=fail)

    assert result["state"] == "failed"
    assert "agent-mcp validate" in result["detail"]
    assert emit(result) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"


def test_payload_command_does_not_create_bridge_configuration(
    monkeypatch, capsys, tmp_path
):
    bridges = tmp_path / "bridges"
    monkeypatch.setattr(config, "BRIDGES_DIR", bridges)
    monkeypatch.setattr(config, "discover_plugin_bridge_candidates", lambda: [])

    assert cli.main(["installer-readiness"]) == 0
    assert not bridges.exists()
    assert json.loads(capsys.readouterr().out)["state"] == "configuration-empty"


def test_duplicate_normalized_names_fail_before_content_validation(tmp_path):
    first = tmp_path / "one" / "demo.yaml"
    second = tmp_path / "two" / "demo.mcp.json"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("fixture\n", encoding="utf-8")
    second.write_text("{}\n", encoding="utf-8")
    loaded = []

    result = evaluate(
        [("demo", first), ("demo", second)],
        loader=lambda path: loaded.append(path),
    )

    assert result["state"] == "failed"
    assert "ambiguous" in result["detail"]
    assert str(first.resolve()) in result["detail"]
    assert str(second.resolve()) in result["detail"]
    assert loaded == []


def test_windows_equivalent_names_and_mcp_suffix_normalize():
    assert normalize_bridge_name(
        "Demo.MCP.yaml", case_insensitive=True
    ) == "demo"
    assert normalize_bridge_name(
        "demo.json", case_insensitive=True
    ) == "demo"
