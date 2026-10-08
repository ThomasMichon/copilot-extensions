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
#
# Both routes through the identical restricted-member rescue path
# `down <fleet>`/`rm <fleet>` already apply per-member (session-rescue-parity
# follow-up, #session-coverage-audit-8169-adjacent) -- a single-container
# stop/remove must never be a cheaper way to skip the evidence-rescue
# safety net the fleet-wide commands enforce.

def test_stop_refuses_a_leased_container(monkeypatch, capsys):
    import agent_containers.lease as lease
    from agent_containers.lifecycle import cmd_stop

    class _Lease:
        effort = "3bac"

    monkeypatch.setattr(lease, "get_lease", lambda name: _Lease())
    rc = cmd_stop(ContainersConfig(), "box-1")
    assert rc == 1
    assert "leased to 3bac" in capsys.readouterr().err


def test_stop_calls_stop_container_when_unleased_and_untracked(monkeypatch, capsys):
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle

    monkeypatch.setattr(lease, "get_lease", lambda name: None)
    monkeypatch.setattr(lifecycle, "get_container", lambda config, name: None)
    seen = []
    monkeypatch.setattr(lifecycle, "stop_container", lambda name: seen.append(name))
    rc = lifecycle.cmd_stop(ContainersConfig(), "box-1")
    assert rc == 0
    assert seen == ["box-1"]
    assert "Stopped: box-1" in capsys.readouterr().out


def test_stop_surfaces_lifecycle_errors(monkeypatch, capsys):
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle

    monkeypatch.setattr(lease, "get_lease", lambda name: None)
    monkeypatch.setattr(lifecycle, "get_container", lambda config, name: None)

    def _boom(name):
        raise RuntimeError(f"docker stop {name} failed: boom")

    monkeypatch.setattr(lifecycle, "stop_container", _boom)
    rc = lifecycle.cmd_stop(ContainersConfig(), "box-1")
    assert rc == 1
    assert "boom" in capsys.readouterr().err


def test_stop_routes_a_restricted_running_member_through_rescue(monkeypatch, capsys):
    """A restricted member must be stopped via `stop_restricted_member`
    (the rescue-gated path), never the bare `stop_container` call -- parity
    with `down <fleet>`'s own per-member admission check."""
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle
    from agent_containers.config import FleetConfig
    from agent_containers.lifecycle import DockerContainerInfo
    from agent_containers.replacement import DestructiveResult

    monkeypatch.setattr(lease, "get_lease", lambda name: None)
    info = DockerContainerInfo(
        name="box-1", container_id="abc123", image="img", state="running",
        status="Up", labels={}, fleet="sandbox", security_profile="restricted",
    )
    monkeypatch.setattr(lifecycle, "get_container", lambda config, name: info)

    def _bare_stop_should_not_run(name):
        raise AssertionError("bare stop_container must not run for a restricted member")

    monkeypatch.setattr(lifecycle, "stop_container", _bare_stop_should_not_run)

    import agent_containers.replacement as replacement
    calls = []

    def _fake_stop_restricted_member(config, fleet, info, *, force_abandon):
        calls.append((fleet, info.name, force_abandon))
        return DestructiveResult(name=info.name, status="stopped")

    monkeypatch.setattr(replacement, "stop_restricted_member", _fake_stop_restricted_member)

    config = ContainersConfig()
    fleet = FleetConfig(security_profile="restricted")
    config.fleets["sandbox"] = fleet

    rc = lifecycle.cmd_stop(config, "box-1")

    assert rc == 0
    assert calls == [(fleet, "box-1", False)]
    assert "Stopped: box-1" in capsys.readouterr().out


def test_stop_defers_a_restricted_looking_member_with_no_matching_fleet(monkeypatch, capsys):
    """Mirrors down_fleet's exact admission check: a container that LOOKS
    restricted but has no matching restricted fleet in the current config
    is deferred -- never silently stopped (and never rescued) unrescued."""
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle
    from agent_containers.lifecycle import DockerContainerInfo

    monkeypatch.setattr(lease, "get_lease", lambda name: None)
    info = DockerContainerInfo(
        name="box-1", container_id="abc123", image="img", state="running",
        status="Up", labels={}, fleet=None, security_profile="restricted",
    )
    monkeypatch.setattr(lifecycle, "get_container", lambda config, name: info)

    def _bare_stop_should_not_run(name):
        raise AssertionError("bare stop_container must not run for an unmatched restricted member")

    monkeypatch.setattr(lifecycle, "stop_container", _bare_stop_should_not_run)

    rc = lifecycle.cmd_stop(ContainersConfig(), "box-1")

    assert rc == 75
    assert "no matching restricted fleet configuration" in capsys.readouterr().err


def test_stop_reports_busy_exit_when_rescue_defers(monkeypatch, capsys):
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle
    from agent_containers.config import FleetConfig
    from agent_containers.lifecycle import DockerContainerInfo
    from agent_containers.replacement import DestructiveResult

    monkeypatch.setattr(lease, "get_lease", lambda name: None)
    info = DockerContainerInfo(
        name="box-1", container_id="abc123", image="img", state="running",
        status="Up", labels={}, fleet="sandbox", security_profile="restricted",
    )
    monkeypatch.setattr(lifecycle, "get_container", lambda config, name: info)

    import agent_containers.replacement as replacement
    monkeypatch.setattr(
        replacement, "stop_restricted_member",
        lambda *_a, **_k: DestructiveResult(name="box-1", status="deferred", reason="active session"),
    )

    config = ContainersConfig()
    config.fleets["sandbox"] = FleetConfig(security_profile="restricted")

    rc = lifecycle.cmd_stop(config, "box-1")

    assert rc == 75
    assert "active session" in capsys.readouterr().err


def test_remove_refuses_a_leased_container(monkeypatch, capsys):
    import agent_containers.lease as lease
    from agent_containers.lifecycle import cmd_remove

    class _Lease:
        effort = "3bac"

    monkeypatch.setattr(lease, "get_lease", lambda name: _Lease())
    rc = cmd_remove(ContainersConfig(), "box-1", force=False)
    assert rc == 1
    assert "leased to 3bac" in capsys.readouterr().err


def test_remove_calls_remove_container_when_unleased_and_untracked(monkeypatch, capsys):
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle

    monkeypatch.setattr(lease, "get_lease", lambda name: None)
    monkeypatch.setattr(lifecycle, "get_container", lambda config, name: None)
    seen = []
    monkeypatch.setattr(
        lifecycle,
        "remove_container",
        lambda name, force=False: seen.append((name, force)),
    )
    rc = lifecycle.cmd_remove(ContainersConfig(), "box-1", force=True)
    assert rc == 0
    assert seen == [("box-1", True)]
    assert "Removed: box-1" in capsys.readouterr().out


def test_remove_routes_a_restricted_member_through_rescue(monkeypatch, capsys):
    """Mirrors the stop-side test: a restricted member's removal must go
    through `destroy_restricted_member` (rescue-then-remove), never the bare
    `remove_container` call."""
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle
    from agent_containers.config import FleetConfig
    from agent_containers.lifecycle import DockerContainerInfo
    from agent_containers.replacement import DestructiveResult

    monkeypatch.setattr(lease, "get_lease", lambda name: None)
    info = DockerContainerInfo(
        name="box-1", container_id="abc123", image="img", state="exited",
        status="Exited", labels={}, fleet="sandbox", security_profile="restricted",
    )
    monkeypatch.setattr(lifecycle, "get_container", lambda config, name: info)

    def _bare_remove_should_not_run(name, force=False):
        raise AssertionError("bare remove_container must not run for a restricted member")

    monkeypatch.setattr(lifecycle, "remove_container", _bare_remove_should_not_run)

    import agent_containers.replacement as replacement
    calls = []

    def _fake_destroy_restricted_member(config, fleet, info, *, operation, force_remove, force_abandon, migrating=False):
        calls.append((fleet, info.name, operation, force_remove, migrating))
        return DestructiveResult(name=info.name, status="removed")

    monkeypatch.setattr(replacement, "destroy_restricted_member", _fake_destroy_restricted_member)

    config = ContainersConfig()
    fleet = FleetConfig(security_profile="restricted")
    config.fleets["sandbox"] = fleet

    rc = lifecycle.cmd_remove(config, "box-1", force=True)

    assert rc == 0
    assert calls == [(fleet, "box-1", "remove", True, False)]
    assert "Removed: box-1" in capsys.readouterr().out


def test_remove_defers_a_restricted_looking_member_with_no_matching_fleet(monkeypatch, capsys):
    """Same no-matching-fleet admission check as the stop side."""
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle
    from agent_containers.lifecycle import DockerContainerInfo

    monkeypatch.setattr(lease, "get_lease", lambda name: None)
    info = DockerContainerInfo(
        name="box-1", container_id="abc123", image="img", state="exited",
        status="Exited", labels={}, fleet=None, security_profile="restricted",
    )
    monkeypatch.setattr(lifecycle, "get_container", lambda config, name: info)

    def _bare_remove_should_not_run(name, force=False):
        raise AssertionError("bare remove_container must not run for an unmatched restricted member")

    monkeypatch.setattr(lifecycle, "remove_container", _bare_remove_should_not_run)

    rc = lifecycle.cmd_remove(ContainersConfig(), "box-1", force=True)

    assert rc == 75
    assert "no matching restricted fleet configuration" in capsys.readouterr().err


def test_remove_passes_migrating_true_for_a_drifted_restricted_member(monkeypatch, capsys):
    """A container whose OWN discovered profile is restricted, under a fleet
    whose current containers.yaml config has since relaxed to trusted, must
    still be rescued -- with migrating=True (the same conformance-skip
    `remove_fleet`'s profile-drift path uses)."""
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle
    from agent_containers.config import FleetConfig
    from agent_containers.lifecycle import DockerContainerInfo
    from agent_containers.replacement import DestructiveResult

    monkeypatch.setattr(lease, "get_lease", lambda name: None)
    info = DockerContainerInfo(
        name="box-1", container_id="abc123", image="img", state="exited",
        status="Exited", labels={}, fleet="sandbox", security_profile="restricted",
    )
    monkeypatch.setattr(lifecycle, "get_container", lambda config, name: info)

    config = ContainersConfig()
    config.fleets["sandbox"] = FleetConfig(security_profile="trusted")

    import agent_containers.replacement as replacement
    calls = []

    def _fake_destroy_restricted_member(config, fleet, info, *, operation, force_remove, force_abandon, migrating=False):
        calls.append(migrating)
        return DestructiveResult(name=info.name, status="removed")

    monkeypatch.setattr(replacement, "destroy_restricted_member", _fake_destroy_restricted_member)

    rc = lifecycle.cmd_remove(config, "box-1", force=False)

    assert rc == 0
    assert calls == [True]


def test_remove_defers_a_drifted_member_with_no_supported_migration_path(monkeypatch, capsys):
    """A container whose OWN discovered profile is neither 'restricted' nor
    matching the fleet's current config (e.g. an unlabeled/'unknown' legacy
    member) has no safe migration path and must be deferred for manual
    recreation -- never silently removed unrescued."""
    import agent_containers.lease as lease
    import agent_containers.lifecycle as lifecycle
    from agent_containers.config import FleetConfig
    from agent_containers.lifecycle import DockerContainerInfo

    monkeypatch.setattr(lease, "get_lease", lambda name: None)
    info = DockerContainerInfo(
        name="box-1", container_id="abc123", image="img", state="exited",
        status="Exited", labels={}, fleet="sandbox", security_profile="unknown",
    )
    monkeypatch.setattr(lifecycle, "get_container", lambda config, name: info)

    def _bare_remove_should_not_run(name, force=False):
        raise AssertionError("bare remove_container must not run for a drifted-unknown member")

    monkeypatch.setattr(lifecycle, "remove_container", _bare_remove_should_not_run)

    config = ContainersConfig()
    config.fleets["sandbox"] = FleetConfig(security_profile="trusted")

    rc = lifecycle.cmd_remove(config, "box-1", force=True)

    assert rc == 75
    assert "no supported migration path" in capsys.readouterr().err


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
