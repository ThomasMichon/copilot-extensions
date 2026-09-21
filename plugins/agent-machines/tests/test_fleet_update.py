"""Tests for agent-machines' fleet-update tier -- mirrors the shape of
``test_self_update.py``'s scheduling-engine coverage (manifest validation,
resource selection, locking, register/reconcile/status, CLI, and the Linux
systemd --user path), scoped to fleet-update's single ``sweep`` tier and its
much simpler business logic (one subprocess call to ``worktree-manager
update`` instead of self-update's dtssh/repo-sweep steps).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_machines import __main__ as cli
from agent_machines import (
    cli_fleet_update,
    fleet_update,
    fleet_update_lock,
    fleet_update_state,
    fleet_update_tasks,
)
from agent_machines.manifest import ManifestError, load_package
from agent_machines.reconcile import plan
from agent_machines.resources import ResourceContext, apply_resources, resolve_resources

from ._helpers import base_package, write_package


def _pkg(
    tmp_path: Path,
    name: str,
    resources: list[dict],
    *,
    authority: int | None = None,
) -> object:
    data = base_package(
        name=name,
        schema_version=4,
        gate=["box-1"],
        manage={},
        resources=resources,
    )
    if authority is not None:
        data["authority"] = authority
    path = write_package(tmp_path / name.replace("/", "_"), "pkg.yaml", data)
    return load_package(path, source_repo=name.split("/")[0])


def _ctx(tmp_path, runner, plat="windows"):
    return ResourceContext(
        home=tmp_path, repo_paths={"acme": tmp_path}, platform=plat, runner=runner
    )


class _FakeMutex:
    def __init__(self, outcome: str):
        self.outcome = outcome
        self.released = False
        self.closed = False

    def try_acquire(self) -> str:
        return self.outcome

    def release(self) -> None:
        self.released = True

    def close(self) -> None:
        self.closed = True


def _task_snapshot(
    tier: str,
    *,
    present: bool,
    matching: bool = True,
    enabled: bool | None = True,
):
    return fleet_update.ScheduledTaskSnapshot(
        task_name=fleet_update_state.TIER_SPECS[tier].task_name,
        present=present,
        enabled=enabled,
        logon_type="Interactive" if present else None,
        description=fleet_update.task_description(tier) if present else None,
        execute="conhost.exe" if present else None,
        arguments=fleet_update_tasks.task_action_arguments(tier),
        working_directory=fleet_update_tasks.task_working_directory(),
        matching=matching,
    )


# --------------------------------------------------------------------------- #
# Manifest validation + resource selection
# --------------------------------------------------------------------------- #
def test_manifest_accepts_fleet_update_resource_and_rejects_bad_tier(tmp_path):
    package = _pkg(
        tmp_path,
        "acme/fleet",
        [{"type": "fleet-update", "tier": "sweep"}],
    )
    assert package.resources[0]["tier"] == "sweep"
    with pytest.raises(ManifestError, match="fleet-update tier"):
        _pkg(
            tmp_path,
            "acme/invalid",
            [{"type": "fleet-update", "tier": "watchdog"}],
        )


def test_fleet_update_resource_selects_highest_authority_and_equal_highest_conflicts(
    tmp_path,
):
    disabled = _pkg(
        tmp_path,
        "acme/disabled",
        [{"type": "fleet-update", "tier": "sweep", "state": "absent"}],
        authority=0,
    )
    enabled = _pkg(
        tmp_path,
        "acme/enabled",
        [{"type": "fleet-update", "tier": "sweep", "state": "present"}],
        authority=10,
    )
    resolved, findings = resolve_resources([disabled, enabled], "box-1", "windows")
    sweep = next(resource for resource in resolved if resource.type == "fleet-update")
    assert sweep.desired["state"] == "present"
    assert any(finding.code == "authority-supersession" for finding in findings)
    equal = _pkg(
        tmp_path,
        "acme/equal",
        [{"type": "fleet-update", "tier": "sweep", "state": "absent"}],
        authority=10,
    )
    _, equal_findings = resolve_resources([equal, enabled], "box-1", "windows")
    assert any(finding.code == "resource-conflict" for finding in equal_findings)


def test_plan_includes_fleet_update_observed_status(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    fleet_update.write_status(
        tmp_path,
        "sweep",
        attempt="2026-09-14T09:00:00+00:00",
        success="2026-09-14T09:05:00+00:00",
    )
    package = _pkg(
        tmp_path,
        "acme/fleet",
        [{"type": "fleet-update", "tier": "sweep"}],
    )
    current = plan([package], "box-1", "windows")
    resource = next(item for item in current.resources if item["type"] == "fleet-update")
    assert resource["observed"] == {
        "last_attempt": "2026-09-14T09:00:00+00:00",
        "last_success": "2026-09-14T09:05:00+00:00",
    }
    assert "last-attempt=2026-09-14T09:00:00+00:00" in resource["summary"]


# --------------------------------------------------------------------------- #
# run_tier
# --------------------------------------------------------------------------- #
def test_run_noops_when_tier_not_opted_in():
    result = fleet_update.run_tier("sweep", opted_in=False)
    assert result.ok is True
    assert result.status == "noop"
    assert result.detail == "tier 'sweep' is not opted in"


def test_run_tier_rejects_unknown_tier():
    with pytest.raises(ValueError, match="unknown fleet-update tier"):
        fleet_update.run_tier("nightly", opted_in=True)


def test_run_tier_invokes_worktree_manager_update_and_records_success(tmp_path):
    calls: list[list[str]] = []

    def runner(argv, timeout=3600):
        calls.append(argv)
        return fleet_update.CommandResult(argv, 0, "updated 12 plugins", "")

    result = fleet_update.run_tier("sweep", opted_in=True, runner=runner, home=tmp_path)
    assert result.ok is True
    assert result.status == "ok"
    assert calls == [["worktree-manager", "update"]]
    assert result.success_at is not None
    status = fleet_update_state.tier_status(tmp_path, "sweep")
    assert status.last_success == result.success_at


def test_run_tier_reports_error_when_worktree_manager_update_fails(tmp_path):
    def runner(argv, timeout=3600):
        return fleet_update.CommandResult(argv, 1, "", "network unreachable")

    result = fleet_update.run_tier("sweep", opted_in=True, runner=runner, home=tmp_path)
    assert result.ok is False
    assert result.status == "error"
    assert "network unreachable" in result.detail
    status = fleet_update_state.tier_status(tmp_path, "sweep")
    assert status.last_success is None


# --------------------------------------------------------------------------- #
# Locking (mirrors self-update's TierLock coverage exactly)
# --------------------------------------------------------------------------- #
def test_lock_refuses_live_owner_without_double_drive(tmp_path, monkeypatch):
    started = datetime(2026, 1, 1, tzinfo=UTC)
    fleet_update.lock_path("sweep", tmp_path).parent.mkdir(parents=True, exist_ok=True)
    fleet_update.lock_path("sweep", tmp_path).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tier": "sweep",
                "pid": 4321,
                "started_at": started.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fleet_update_lock, "_WindowsMutex", lambda _name: _FakeMutex("timeout"))
    monkeypatch.setattr(fleet_update_state, "_WindowsMutex", lambda _name: _FakeMutex("timeout"))
    monkeypatch.setattr(fleet_update.sys, "platform", "win32")
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "win32")
    lock = fleet_update.TierLock(tier="sweep", home=tmp_path)
    acquired, detail = lock.acquire()
    assert acquired is False
    assert "already active" in detail
    assert lock.snapshot is not None
    assert lock.snapshot.pid == 4321


def test_lock_reclaims_dead_owner_only_after_stale_window(tmp_path, monkeypatch):
    started = datetime(2026, 1, 1, tzinfo=UTC)
    path = fleet_update.lock_path("sweep", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tier": "sweep",
                "pid": 4321,
                "started_at": started.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fleet_update_lock, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(fleet_update_state, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(fleet_update.sys, "platform", "win32")
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "win32")
    lock = fleet_update.TierLock(
        tier="sweep",
        home=tmp_path,
        pid=9999,
        now=lambda: started + timedelta(hours=27),
        pid_alive=lambda _pid: False,
    )
    acquired, detail = lock.acquire()
    assert acquired is True
    assert detail == "acquired"
    assert lock.reclaimed is True
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["pid"] == 9999
    lock.release()


def test_tier_lock_acquires_and_releases_on_posix(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX-only lock path")
    lock = fleet_update.TierLock(tier="sweep", home=tmp_path)
    acquired, detail = lock.acquire()
    assert acquired is True
    assert detail == "acquired"
    assert fleet_update.lock_path("sweep", tmp_path).exists()
    lock.release()
    assert not fleet_update.lock_path("sweep", tmp_path).exists()


def test_tier_lock_refuses_concurrent_holder_on_posix(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX-only lock path")
    first = fleet_update.TierLock(tier="sweep", home=tmp_path, pid=111)
    acquired_first, _ = first.acquire()
    assert acquired_first is True

    second = fleet_update.TierLock(
        tier="sweep", home=tmp_path, pid=222, pid_alive=lambda _p: True
    )
    acquired_second, detail = second.acquire()
    assert acquired_second is False
    assert "already active" in detail
    first.release()


# --------------------------------------------------------------------------- #
# Windows Scheduled Task register/reconcile/status
# --------------------------------------------------------------------------- #
def test_reconcile_task_registers_new_opt_in(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_update.sys, "platform", "win32")
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "win32")
    monkeypatch.setattr(fleet_update_lock, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(fleet_update_state, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    state = {"present": False}

    def query(tier, *, runner=None, resolve_binary=None, home=None):
        return _task_snapshot(tier, present=state["present"])

    def register(tier, *, runner=None, resolve_binary=None, home=None):
        state["present"] = True
        return fleet_update.CommandResult(["pwsh"], 0, "", "")

    monkeypatch.setattr(fleet_update_tasks, "query_scheduled_task", query)
    monkeypatch.setattr(fleet_update_tasks, "register_scheduled_task", register)
    result = fleet_update.reconcile_scheduled_task("sweep", desired_present=True, home=tmp_path)
    assert result.status == "changed"
    assert result.changed is True
    assert result.snapshot is not None and result.snapshot.present is True


def test_register_scheduled_task_daily_has_no_repetition_parameters():
    captured: dict[str, list[str]] = {}

    def fake_runner(argv, *, timeout=300):
        captured["argv"] = argv
        return fleet_update.CommandResult(argv, 0, "", "")

    fleet_update_tasks.register_scheduled_task(
        "sweep",
        runner=fake_runner,
        resolve_binary=lambda name: f"/usr/bin/{name}",
        home=Path("/home/operator"),
    )
    script = captured["argv"][-1]
    assert "-Daily -At '4:00AM' -DaysInterval 1" in script
    assert "-RepetitionInterval" not in script


def test_task_action_arguments_uses_worktree_manager_binstub(monkeypatch):
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "win32")
    args = fleet_update_tasks.task_action_arguments("sweep", home=Path(r"C:\Users\operator"))
    assert args.startswith("--headless cmd.exe /c ")
    assert r"C:\Users\operator\.local\bin\worktree-manager.cmd" in args
    assert " update" in args
    assert "self-update" not in args
    assert "agent-machines" not in args


def test_task_definition_matches_its_own_generated_arguments(monkeypatch):
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "win32")
    home = Path(r"C:\Users\operator")
    snapshot = fleet_update_tasks.ScheduledTaskSnapshot(
        task_name=fleet_update_state.TIER_SPECS["sweep"].task_name,
        present=True,
        enabled=True,
        state="Ready",
        logon_type="Interactive",
        description=fleet_update_tasks.task_description("sweep"),
        execute="conhost.exe",
        arguments=fleet_update_tasks.task_action_arguments("sweep", home=home),
        working_directory=fleet_update_tasks.task_working_directory(home),
        trigger_kind="daily",
        trigger_value=1,
    )
    assert fleet_update_tasks.task_definition_matches(snapshot, "sweep", home=home)


def test_reconcile_task_defers_to_elevated_install_when_registration_is_denied(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(fleet_update.sys, "platform", "win32")
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "win32")
    monkeypatch.setattr(fleet_update_lock, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(fleet_update_state, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(
        fleet_update_tasks,
        "query_scheduled_task",
        lambda tier, **kwargs: _task_snapshot(tier, present=False, matching=False),
    )
    monkeypatch.setattr(
        fleet_update_tasks,
        "register_scheduled_task",
        lambda tier, **kwargs: fleet_update.CommandResult(["pwsh"], 1, "", "Access is denied."),
    )
    result = fleet_update.reconcile_scheduled_task("sweep", desired_present=True, home=tmp_path)
    assert result.status == "deferred"
    assert result.ok is False
    assert result.commands == [["agent-machines", "fleet-update", "install", "--tier", "sweep"]]
    assert "run once from an elevated PowerShell" in result.detail


def test_reconcile_task_removes_opted_out_task_without_retry_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_update.sys, "platform", "win32")
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "win32")
    monkeypatch.setattr(fleet_update_lock, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(fleet_update_state, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(
        fleet_update_tasks,
        "query_scheduled_task",
        lambda tier, **kwargs: _task_snapshot(tier, present=True, matching=True, enabled=True),
    )
    calls: list[str] = []

    def unregister(tier, *, runner=None, resolve_binary=None):
        calls.append(tier)
        return fleet_update.CommandResult(["pwsh"], 0, "", "")

    monkeypatch.setattr(fleet_update_tasks, "unregister_scheduled_task", unregister)
    result = fleet_update.reconcile_scheduled_task("sweep", desired_present=False, home=tmp_path)
    assert result.status == "changed"
    assert result.changed is True
    assert calls == ["sweep"]
    assert "elevated PowerShell" not in result.detail


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_cli_fleet_update_run_emits_json(monkeypatch, capsys):
    monkeypatch.setattr(
        cli_fleet_update,
        "_resolve_machine_identity",
        lambda args: type(
            "Identity",
            (),
            {"canonical": "box-1", "accepted": ("box-1",), "warnings": [], "raw": "box-1"},
        )(),
    )
    monkeypatch.setattr(cli_fleet_update, "_emit_identity_warnings", lambda identity: None)
    monkeypatch.setattr(
        cli_fleet_update, "_collect_all_packages", lambda machine, accepted_machines=None: []
    )
    monkeypatch.setattr(
        cli_fleet_update._reconcile,
        "resolve_union",
        lambda packages, machine, accepted_machines=None: [],
    )
    monkeypatch.setattr(
        cli_fleet_update._validator, "validate", lambda resolved, machine, plat=None: []
    )
    monkeypatch.setattr(
        cli_fleet_update._resources, "resolve_resources", lambda resolved, machine, plat: ([], [])
    )
    monkeypatch.setattr(
        cli_fleet_update._fleet_update,
        "run_tier",
        lambda tier, **kwargs: fleet_update.RunResult(
            tier=tier,
            status="noop",
            opted_in=False,
            detail="tier 'sweep' is not opted in",
        ),
    )
    rc = cli.main(["fleet-update", "run", "--tier", "sweep", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["tier"] == "sweep"
    assert payload["status"] == "noop"


def test_cli_fleet_update_install_skips_non_opted_in_tier_without_registration(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        cli_fleet_update,
        "_resolve_machine_identity",
        lambda args: type(
            "Identity",
            (),
            {"canonical": "box-1", "accepted": ("box-1",), "warnings": [], "raw": "box-1"},
        )(),
    )
    monkeypatch.setattr(cli_fleet_update, "_emit_identity_warnings", lambda identity: None)
    monkeypatch.setattr(
        cli_fleet_update, "_collect_all_packages", lambda machine, accepted_machines=None: []
    )
    monkeypatch.setattr(
        cli_fleet_update._reconcile,
        "resolve_union",
        lambda packages, machine, accepted_machines=None: [],
    )
    monkeypatch.setattr(
        cli_fleet_update._validator, "validate", lambda resolved, machine, plat=None: []
    )
    monkeypatch.setattr(
        cli_fleet_update._resources, "resolve_resources", lambda resolved, machine, plat: ([], [])
    )
    registered = []
    monkeypatch.setattr(
        cli_fleet_update._fleet_update,
        "reconcile_scheduled_task",
        lambda tier, **kwargs: registered.append(tier),
    )
    rc = cli.main(["fleet-update", "install", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert registered == []
    assert payload["tiers"][0]["status"] == "skipped"


def test_cli_fleet_update_status_emits_json(monkeypatch, capsys):
    monkeypatch.setattr(
        cli_fleet_update,
        "_resolve_machine_identity",
        lambda args: type(
            "Identity",
            (),
            {"canonical": "box-1", "accepted": ("box-1",), "warnings": [], "raw": "box-1"},
        )(),
    )
    monkeypatch.setattr(cli_fleet_update, "_emit_identity_warnings", lambda identity: None)
    monkeypatch.setattr(
        cli_fleet_update, "_collect_all_packages", lambda machine, accepted_machines=None: []
    )
    monkeypatch.setattr(
        cli_fleet_update._reconcile,
        "resolve_union",
        lambda packages, machine, accepted_machines=None: [],
    )
    monkeypatch.setattr(
        cli_fleet_update._validator, "validate", lambda resolved, machine, plat=None: []
    )
    monkeypatch.setattr(
        cli_fleet_update._resources, "resolve_resources", lambda resolved, machine, plat: ([], [])
    )
    monkeypatch.setattr(
        cli_fleet_update._fleet_update,
        "scheduled_task_status",
        lambda tier, **kwargs: fleet_update.ScheduledTaskStatus(
            tier=tier,
            opted_in=False,
            task_name=fleet_update_state.TIER_SPECS[tier].task_name,
            registered=False,
            detail="Scheduled Task is not registered",
        ),
    )
    rc = cli.main(["fleet-update", "status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["tiers"][0]["registered"] is False


# --------------------------------------------------------------------------- #
# Linux/WSL: systemd --user timers
# --------------------------------------------------------------------------- #
def test_render_linux_timer_unit_daily():
    unit = fleet_update_tasks.render_linux_timer_unit("sweep")
    assert "OnCalendar=*-*-* 04:00:00" in unit
    assert "Persistent=true" in unit


def test_render_linux_service_unit_includes_workdir(tmp_path):
    unit = fleet_update_tasks.render_linux_service_unit("sweep", home=tmp_path)
    assert "Type=oneshot" in unit
    assert str(tmp_path / ".agent-machines") in unit
    assert "worktree-manager" in unit


def test_linux_systemd_user_available_false_without_binary():
    assert (
        fleet_update_tasks.linux_systemd_user_available(
            resolve_binary=lambda _b: None, runner=lambda *a, **k: None
        )
        is False
    )


def _fake_command_result(argv, returncode, stdout=""):
    return fleet_update.CommandResult(argv, returncode, stdout, "")


def test_query_systemd_timer_matches_after_register(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "linux")

    def runner(argv, **kwargs):
        if "is-system-running" in argv:
            return _fake_command_result(argv, 0, "running\n")
        if "is-enabled" in argv:
            return _fake_command_result(argv, 0, "enabled\n")
        if "is-active" in argv:
            return _fake_command_result(argv, 0, "active\n")
        return _fake_command_result(argv, 0)

    fleet_update_tasks.register_systemd_timer(
        "sweep", runner=runner, resolve_binary=lambda name: f"/usr/bin/{name}", home=tmp_path
    )
    snapshot = fleet_update_tasks.query_systemd_timer(
        "sweep",
        runner=runner,
        resolve_binary=lambda name: f"/usr/bin/{name}",
        home=tmp_path,
    )
    assert snapshot.present is True
    assert snapshot.enabled is True
    assert snapshot.state == "active"
    assert snapshot.matching is True
    assert snapshot.execute == str(tmp_path / ".local" / "bin" / "worktree-manager")


def test_unregister_systemd_timer_removes_units_and_disables(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "linux")
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return _fake_command_result(argv, 0)

    fleet_update_tasks.register_systemd_timer(
        "sweep", runner=runner, resolve_binary=lambda name: f"/usr/bin/{name}", home=tmp_path
    )
    fleet_update_tasks.unregister_systemd_timer(
        "sweep", runner=runner, resolve_binary=lambda name: f"/usr/bin/{name}", home=tmp_path
    )
    assert not fleet_update_tasks._linux_service_unit_path("sweep", tmp_path).exists()
    assert not fleet_update_tasks._linux_timer_unit_path("sweep", tmp_path).exists()


def test_reconcile_scheduled_task_registers_on_linux_when_systemd_available(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "linux")
    monkeypatch.setattr(fleet_update, "shutil_which", lambda _b: "/usr/bin/systemctl")

    def runner(argv, **kwargs):
        if "is-system-running" in argv:
            return _fake_command_result(argv, 0, "running\n")
        if "is-enabled" in argv:
            return _fake_command_result(argv, 0, "enabled\n")
        if "is-active" in argv:
            return _fake_command_result(argv, 0, "active\n")
        return _fake_command_result(argv, 0)

    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=True,
        runner=runner,
        home=tmp_path,
    )
    assert result.status == "changed"
    assert result.changed is True


def test_reconcile_scheduled_task_skips_when_no_systemd_user_manager(monkeypatch, tmp_path):
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "linux")
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=True,
        runner=lambda *a, **k: _fake_command_result(a[0] if a else [], 1),
        home=tmp_path,
    )
    assert result.status == "skipped"
    assert result.changed is False


# --------------------------------------------------------------------------- #
# Declarative resource handler (apply/dry-run), mirroring self-update's
# resource tests in test_resources.py.
# --------------------------------------------------------------------------- #
def test_fleet_update_resource_registers_opted_in_task(tmp_path, monkeypatch):
    from agent_machines.resources import RunOutcome

    class FakeRunner:
        def __call__(self, argv):
            return RunOutcome(0, "", "")

    pkg = _pkg(tmp_path, "acme/fleet", [{"type": "fleet-update", "tier": "sweep"}])
    monkeypatch.setattr(
        "agent_machines.resource_fleet_update._fleet_update.reconcile_scheduled_task",
        lambda tier, **kwargs: fleet_update.ScheduledTaskReconcileResult(
            tier=tier,
            desired_state="present",
            status="changed",
            changed=True,
            detail="registered the Scheduled Task",
        ),
    )
    results = apply_resources(
        [pkg],
        "box-1",
        "windows",
        _ctx(tmp_path, FakeRunner()),
        dry_run=False,
    )
    res = results[0]
    assert res.type == "fleet-update"
    assert res.action == "install"
    assert res.changed is True
    assert res.detail == "registered the Scheduled Task"


def test_fleet_update_resource_defers_registration_with_install_retry(tmp_path, monkeypatch):
    from agent_machines.resources import RunOutcome

    class FakeRunner:
        def __call__(self, argv):
            return RunOutcome(0, "", "")

    pkg = _pkg(tmp_path, "acme/fleet", [{"type": "fleet-update", "tier": "sweep"}])
    monkeypatch.setattr(
        "agent_machines.resource_fleet_update._fleet_update.reconcile_scheduled_task",
        lambda tier, **kwargs: fleet_update.ScheduledTaskReconcileResult(
            tier=tier,
            desired_state="present",
            status="deferred",
            changed=False,
            detail=(
                "Scheduled Task registration needs elevation -- run once from an "
                "elevated PowerShell to install the Scheduled Task: "
                "agent-machines fleet-update install --tier sweep"
            ),
            commands=[["agent-machines", "fleet-update", "install", "--tier", tier]],
            attempted_elevation=True,
        ),
    )
    results = apply_resources(
        [pkg],
        "box-1",
        "windows",
        _ctx(tmp_path, FakeRunner()),
        dry_run=False,
    )
    res = results[0]
    assert res.status == "deferred"
    assert res.deferred_reason is not None and "elevated PowerShell" in res.deferred_reason
    assert res.commands == [["agent-machines", "fleet-update", "install", "--tier", "sweep"]]
