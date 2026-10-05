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


# --- stop/remove (single-container, picker-venue-pivots Phase 2) ----------
# Implemented in lifecycle.py (cmd_stop/cmd_remove); __main__.py's dispatch
# is a thin call-through, kept out of __main__.py's own module-size budget.

def test_stop_refuses_a_leased_container(monkeypatch, capsys):
    import agent_containers.lease as lease
    from agent_containers.lifecycle import cmd_stop

    class _Lease:
        effort = "3bac"

    monkeypatch.setattr(lease, "get_lease", lambda name: _Lease())
    rc = cmd_stop("box-1")
    assert rc == 1
    assert "leased to 3bac" in capsys.readouterr().err


def test_stop_calls_stop_container_when_unleased(monkeypatch, capsys):
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle

    monkeypatch.setattr(lease, "get_lease", lambda name: None)
    seen = []
    monkeypatch.setattr(lifecycle, "stop_container", lambda name: seen.append(name))
    rc = lifecycle.cmd_stop("box-1")
    assert rc == 0
    assert seen == ["box-1"]
    assert "Stopped: box-1" in capsys.readouterr().out


def test_stop_surfaces_lifecycle_errors(monkeypatch, capsys):
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle

    monkeypatch.setattr(lease, "get_lease", lambda name: None)

    def _boom(name):
        raise RuntimeError(f"docker stop {name} failed: boom")

    monkeypatch.setattr(lifecycle, "stop_container", _boom)
    rc = lifecycle.cmd_stop("box-1")
    assert rc == 1
    assert "boom" in capsys.readouterr().err


def test_remove_refuses_a_leased_container(monkeypatch, capsys):
    import agent_containers.lease as lease
    from agent_containers.lifecycle import cmd_remove

    class _Lease:
        effort = "3bac"

    monkeypatch.setattr(lease, "get_lease", lambda name: _Lease())
    rc = cmd_remove("box-1", force=False)
    assert rc == 1
    assert "leased to 3bac" in capsys.readouterr().err


def test_remove_calls_remove_container_when_unleased(monkeypatch, capsys):
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle

    monkeypatch.setattr(lease, "get_lease", lambda name: None)
    seen = []
    monkeypatch.setattr(
        lifecycle,
        "remove_container",
        lambda name, force=False: seen.append((name, force)),
    )
    rc = lifecycle.cmd_remove("box-1", force=True)
    assert rc == 0
    assert seen == [("box-1", True)]
    assert "Removed: box-1" in capsys.readouterr().out


def test_append_copilot_args_is_noop_without_extra_args():
    assert cli._append_copilot_args("copilot --acp --stdio", []) == "copilot --acp --stdio"
    assert cli._append_copilot_args("copilot --acp --stdio", None) == "copilot --acp --stdio"


def test_append_copilot_args_quotes_and_appends():
    # A value containing a space must round-trip as one shell argument.
    result = cli._append_copilot_args(
        "copilot --acp --stdio", ["--agent", "some charter"]
    )
    assert result == "copilot --acp --stdio --agent 'some charter'"


def test_cmd_exec_forwards_copilot_args_into_acp_command(monkeypatch):
    """A charter overlay (``copilot_args``, e.g. from an agent-dispatch
    registrar pool's ``body.charter``) passed to ``agent-containers exec``
    must reach the launched in-container acp_command."""
    from agent_containers.resolver import LiveExecTarget

    target = LiveExecTarget(
        name="myfleet-1",
        container_id="abc123",
        config=object(),
        fleet=object(),
        info=object(),
        actual_profile="trusted",
        user="node",
        workspace_folder="/workspace/repo",
        acp_command="copilot --acp --stdio --allow-all",
    )
    monkeypatch.setattr(cli, "resolve_live_exec_target", lambda *a, **k: target)
    monkeypatch.setattr(cli, "load_config", lambda: object())

    import ssh_manager

    class _FakeLock:
        def __init__(self, *a, **k):
            pass

        def acquire(self, force=False):
            pass

        def release(self):
            pass

    monkeypatch.setattr(ssh_manager, "TargetLock", _FakeLock)

    captured_acp_command = {}

    def fake_launch(args, config, fleet, actual_profile, user, acp_command, **kw):
        captured_acp_command["value"] = acp_command
        return 0

    monkeypatch.setattr(cli, "_launch_container_agent", fake_launch)

    args = argparse.Namespace(
        name="myfleet-1", stdio=False, force=False,
        copilot_args=["--agent", "some-charter"],
    )
    rc = cli._cmd_exec(args)

    assert rc == 0
    assert captured_acp_command["value"] == (
        "copilot --acp --stdio --allow-all --agent some-charter"
    )


def test_cmd_exec_is_unchanged_without_copilot_args(monkeypatch):
    """No extra copilot_args (the overwhelming common case today) must
    launch the exact same acp_command as before this change."""
    from agent_containers.resolver import LiveExecTarget

    target = LiveExecTarget(
        name="myfleet-1",
        container_id="abc123",
        config=object(),
        fleet=object(),
        info=object(),
        actual_profile="trusted",
        user="node",
        workspace_folder="/workspace/repo",
        acp_command="copilot --acp --stdio --allow-all",
    )
    monkeypatch.setattr(cli, "resolve_live_exec_target", lambda *a, **k: target)
    monkeypatch.setattr(cli, "load_config", lambda: object())

    import ssh_manager

    class _FakeLock:
        def __init__(self, *a, **k):
            pass

        def acquire(self, force=False):
            pass

        def release(self):
            pass

    monkeypatch.setattr(ssh_manager, "TargetLock", _FakeLock)

    captured_acp_command = {}

    def fake_launch(args, config, fleet, actual_profile, user, acp_command, **kw):
        captured_acp_command["value"] = acp_command
        return 0

    monkeypatch.setattr(cli, "_launch_container_agent", fake_launch)

    args = argparse.Namespace(
        name="myfleet-1", stdio=False, force=False, copilot_args=[],
    )
    rc = cli._cmd_exec(args)

    assert rc == 0
    assert captured_acp_command["value"] == "copilot --acp --stdio --allow-all"
