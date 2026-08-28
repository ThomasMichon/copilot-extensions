"""Machine-readable and busy-exit lifecycle operation tests."""

from __future__ import annotations

import argparse
import json

from agent_containers import __main__ as cli
from agent_containers import fleet as fleet_mod
from agent_containers.config import ContainersConfig


def test_up_json_reports_partial_result_and_busy_exit(monkeypatch, capsys):
    result = fleet_mod.FleetOperationResult(
        created=["sandbox-1"],
        deferred={"sandbox-2": "active session"},
    )
    monkeypatch.setattr(cli, "load_config", ContainersConfig)
    monkeypatch.setattr(fleet_mod, "reconcile_up", lambda *_args, **_kwargs: result)
    args = argparse.Namespace(
        fleet="sandbox",
        count=None,
        recreate=True,
        force_abandon=False,
        json=True,
    )

    rc = cli._cmd_up(args)

    assert rc == 75
    payload = json.loads(capsys.readouterr().out)
    assert payload["created"] == ["sandbox-1"]
    assert payload["deferred"] == {"sandbox-2": "active session"}


def test_down_json_distinguishes_unchanged_and_deferred(monkeypatch, capsys):
    result = fleet_mod.FleetOperationResult(
        unchanged={"sandbox-1": "already stopped"},
        deferred={"sandbox-2": "paused"},
    )
    monkeypatch.setattr(cli, "load_config", ContainersConfig)
    monkeypatch.setattr(fleet_mod, "down_fleet", lambda *_args, **_kwargs: result)
    args = argparse.Namespace(
        command="down",
        fleet="sandbox",
        force_abandon=False,
        json=True,
    )

    rc = cli._cmd_fleet_op(args)

    assert rc == 75
    payload = json.loads(capsys.readouterr().out)
    assert payload["unchanged"] == {"sandbox-1": "already stopped"}
    assert payload["deferred"] == {"sandbox-2": "paused"}
