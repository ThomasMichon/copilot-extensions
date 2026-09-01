from __future__ import annotations

import argparse
import json

from agent_mcp.__main__ import _cmd_status


def _bridge(root, marketplace: str, plugin: str, filename: str):
    directory = root / marketplace / plugin / "agents"
    directory.mkdir(parents=True)
    path = directory / filename
    path.write_text(json.dumps({"server": {"url": "https://example.com"}}))
    return path


def test_status_reports_every_ambiguous_provider(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_MCP_PLUGIN_ROOTS", str(tmp_path))
    _bridge(tmp_path, "market-a", "plugin-a", "demo.mcp.yaml")
    _bridge(tmp_path, "market-b", "plugin-b", "demo.mcp.yaml")

    rc = _cmd_status(argparse.Namespace())

    output = capsys.readouterr().out
    assert rc == 1
    assert "demo [AMBIGUOUS]" in output
    assert "plugin-a@market-a" in output
    assert "plugin-b@market-b" in output
    assert "copilot plugin uninstall plugin-a@market-a" in output
    assert "copilot plugin uninstall plugin-b@market-b" in output
