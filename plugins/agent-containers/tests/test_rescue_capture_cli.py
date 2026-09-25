"""CLI-dispatch tests for ``rescue-capture`` (agent-containers).

Covers the ``rescue_capture_cli.cmd_rescue_capture`` entry point split out of
``__main__.py`` (module-size budget) -- both the human-readable summary and
``--json`` outcome shapes, and the busy exit code on any deferred member.
"""
from __future__ import annotations

import argparse
import json

from agent_containers import fleet as fleet_mod
from agent_containers import rescue_capture_cli as rcc
from agent_containers.config import ContainersConfig


def test_rescue_capture_json_reports_captured_and_deferred(monkeypatch, capsys):
    result = fleet_mod.FleetOperationResult(
        captured=["sandbox-1"],
        rescues={"sandbox-1": {"status": "verified"}},
        deferred={"sandbox-2": "active Copilot session-state lock present"},
    )
    monkeypatch.setattr(rcc, "load_config", ContainersConfig)
    monkeypatch.setattr(fleet_mod, "rescue_capture_fleet", lambda *_a, **_k: result)
    args = argparse.Namespace(fleet="sandbox", json=True)

    rc = rcc.cmd_rescue_capture(args)

    assert rc == 75
    payload = json.loads(capsys.readouterr().out)
    assert payload["captured"] == ["sandbox-1"]
    assert payload["rescues"] == {"sandbox-1": {"status": "verified"}}
    assert payload["deferred"] == {
        "sandbox-2": "active Copilot session-state lock present"
    }


def test_rescue_capture_json_all_captured_is_clean_exit(monkeypatch, capsys):
    result = fleet_mod.FleetOperationResult(captured=["sandbox-1", "sandbox-2"])
    monkeypatch.setattr(rcc, "load_config", ContainersConfig)
    monkeypatch.setattr(fleet_mod, "rescue_capture_fleet", lambda *_a, **_k: result)
    args = argparse.Namespace(fleet="sandbox", json=True)

    rc = rcc.cmd_rescue_capture(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["captured"] == ["sandbox-1", "sandbox-2"]
    assert payload["deferred"] == {}


def test_rescue_capture_text_summary(monkeypatch, capsys):
    result = fleet_mod.FleetOperationResult(
        captured=["sandbox-1"],
        deferred={"sandbox-2": "container state 'exited' is not capturable"},
    )
    monkeypatch.setattr(rcc, "load_config", ContainersConfig)
    monkeypatch.setattr(fleet_mod, "rescue_capture_fleet", lambda *_a, **_k: result)
    args = argparse.Namespace(fleet="sandbox", json=False)

    rc = rcc.cmd_rescue_capture(args)

    assert rc == 75
    out = capsys.readouterr().out
    assert "Captured: sandbox-1" in out
    assert "Deferred: sandbox-2 (container state 'exited' is not capturable)" in out


def test_rescue_capture_text_none_captured(monkeypatch, capsys):
    result = fleet_mod.FleetOperationResult()
    monkeypatch.setattr(rcc, "load_config", ContainersConfig)
    monkeypatch.setattr(fleet_mod, "rescue_capture_fleet", lambda *_a, **_k: result)
    args = argparse.Namespace(fleet="sandbox", json=False)

    rc = rcc.cmd_rescue_capture(args)

    assert rc == 0
    assert "Captured: (none)" in capsys.readouterr().out


def test_rescue_capture_parser_wires_fleet_and_json_and_func():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    rcc.add_rescue_capture_parser(sub)

    args = parser.parse_args(["rescue-capture", "sandbox", "--json"])

    assert args.command == "rescue-capture"
    assert args.fleet == "sandbox"
    assert args.json is True
    assert args.func is rcc.cmd_rescue_capture
