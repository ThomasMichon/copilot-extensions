"""Ownership handoff never starts a competing updater or retires a live job."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from agent_machines import (
    automatic_update_ownership as ownership,
)
from agent_machines import (
    cli_fleet_update,
    fleet_update,
    fleet_update_tasks,
    legacy_update_inventory,
    self_update,
    self_update_tasks,
)
from agent_machines.legacy_fleet_definitions import (
    historical_linux_bodies,
    historical_windows_arguments,
)
from agent_machines.resource_fleet_update import FleetUpdateResourceHandler
from agent_machines.resources import ResolvedResource, ResourceContext

from ._manager_compatibility_helpers import unit_guarded_manager_update
from ._scheduler_safety_helpers import unit_properties


@pytest.fixture(autouse=True)
def isolated_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def write_policy(tmp_path: Path, owner: str = "manager", **overrides: Any) -> Path:
    payload = {
        "schema": "copilot-extensions.automatic-updates",
        "version": 1,
        "owner": owner,
        "provenance": "explicit-user",
        "user_disabled": False,
        "interval_seconds": 3600,
        "project": "example" if owner == "manager" else None,
        "external_controller": "example-controller" if owner == "external" else None,
        **overrides,
    }
    path = tmp_path / "manager" / "automatic-updates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_automatic_ownership_absent_is_standalone(tmp_path: Path) -> None:
    result = ownership.read_update_ownership(tmp_path)
    assert result.state == "standalone"
    assert not result.blocks_legacy_schedule


@pytest.mark.parametrize("owner", ["manager", "external", "disabled"])
def test_automatic_ownership_valid_contract(tmp_path: Path, owner: str) -> None:
    write_policy(tmp_path, owner)
    result = ownership.read_update_ownership(tmp_path)
    assert result.state == owner
    assert result.blocks_legacy_schedule
    assert result.hands_off_suite


@pytest.mark.parametrize("owner", ["manager", "external", "disabled"])
def test_automatic_ownership_nullable_project_requires_non_manager(
    tmp_path: Path,
    owner: str,
) -> None:
    write_policy(tmp_path, owner, project=None)
    result = ownership.read_update_ownership(tmp_path)
    assert result.state == ("refused" if owner == "manager" else owner)
    if owner == "manager":
        assert "requires a nonempty project" in result.detail


@pytest.mark.parametrize(
    "override",
    [
        {"unexpected": True},
        {"version": True},
        {"version": 2},
        {"schema": "other"},
        {"owner": []},
        {"owner": "unknown"},
        {"provenance": " "},
        {"provenance": "line\nbreak"},
        {"user_disabled": 1},
        {"user_disabled": True},
        {"interval_seconds": True},
        {"interval_seconds": 299},
        {"interval_seconds": 86401},
        {"project": ""},
        {"project": []},
        {"external_controller": "conflict"},
    ],
)
def test_automatic_ownership_invalid_contract_refused(
    tmp_path: Path,
    override: dict[str, Any],
) -> None:
    write_policy(tmp_path, **override)
    result = ownership.read_update_ownership(tmp_path)
    assert result.state == "refused"
    assert "refused" in result.detail


def test_automatic_ownership_external_requires_controller(tmp_path: Path) -> None:
    write_policy(tmp_path, "external", external_controller=None)
    assert ownership.read_update_ownership(tmp_path).state == "refused"


@pytest.mark.parametrize("text", ["{", "[]", '{"owner":"manager","owner":"external"}'])
def test_automatic_ownership_invalid_json_refused(tmp_path: Path, text: str) -> None:
    path = write_policy(tmp_path)
    path.write_text(text, encoding="utf-8")
    assert ownership.read_update_ownership(tmp_path).state == "refused"


def test_automatic_ownership_unreadable_is_not_absent(tmp_path: Path) -> None:
    path = tmp_path / "manager" / "automatic-updates.json"
    path.mkdir(parents=True)
    assert ownership.read_update_ownership(tmp_path).state == "refused"


def test_automatic_ownership_user_disable_wins(tmp_path: Path) -> None:
    write_policy(tmp_path, "disabled", user_disabled=True)
    assert ownership.read_update_ownership(tmp_path).state == "disabled"


def test_automatic_ownership_home_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKTREE_MANAGER_ROOT")
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    assert ownership.ownership_policy_path() == (
        tmp_path / "profile" / ".worktree-manager" / "automatic-updates.json"
    )
    assert ownership.ownership_policy_path(tmp_path) == (
        tmp_path / ".worktree-manager" / "automatic-updates.json"
    )


@pytest.mark.parametrize("owner", ["manager", "external", "disabled", "refused", None])
def test_automatic_ownership_self_sweep_preserves_maintenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: str | None,
) -> None:
    if owner is not None:
        path = write_policy(tmp_path, "manager" if owner == "refused" else owner)
        if owner == "refused":
            path.write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        self_update,
        "sweep_repo_paths",
        lambda repos, **kwargs: [self_update.StepResult("git-pull", "ok", "sync preserved")],
    )

    def runner(argv: list[str], **kwargs: Any) -> self_update.CommandResult:
        calls.append(argv)
        return self_update.CommandResult(argv, 0, "ok")

    result = self_update.run_tier(
        "sweep",
        opted_in=True,
        runner=runner,
        home=tmp_path,
    )
    assert result.steps[0].name == "git-pull"
    assert [
        "agent-machines",
        "restore",
        "--apply",
        "--all-projects",
        "--maintenance-safe",
    ] in calls
    assert any(call[0] == "agent-worktrees" for call in calls) is (owner is None)
    assert result.status == ("error" if owner == "refused" else "ok")
    if owner is not None:
        assert any(step.name == "suite-update-ownership" for step in result.steps)


def test_automatic_ownership_watchdog_does_not_depend_on_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_policy(tmp_path).write_text("malformed", encoding="utf-8")
    monkeypatch.setattr(
        self_update,
        "ensure_watchdog",
        lambda **kwargs: [self_update.StepResult("dtssh-launcher", "ok")],
    )
    result = self_update.run_tier(
        "watchdog",
        opted_in=True,
        home=tmp_path,
        host_healer=lambda: self_update.StepResult("host", "ok"),
        mesh_refresher=lambda: self_update.StepResult("mesh", "ok"),
    )
    assert result.ok
    assert [step.name for step in result.steps] == ["dtssh-launcher", "host", "mesh"]


@pytest.mark.parametrize("owner", ["manager", "external", "disabled", "refused", None])
def test_automatic_ownership_fleet_run_admission(
    tmp_path: Path,
    owner: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fleet_update, "run_compatible_manager_update", unit_guarded_manager_update)
    if owner is not None:
        path = write_policy(tmp_path, "manager" if owner == "refused" else owner)
        if owner == "refused":
            path.write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: Any) -> fleet_update.CommandResult:
        calls.append(argv)
        return fleet_update.CommandResult(argv, 0, "ok")

    result = fleet_update.run_tier(
        "sweep",
        opted_in=True,
        runner=runner,
        home=tmp_path,
    )
    allowed = owner is None
    assert bool(calls) is allowed
    assert result.ok is allowed
    if not allowed:
        assert result.status == ("error" if owner == "refused" else "deferred")


@pytest.mark.parametrize("owner", ["manager", "external", "disabled", "refused"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_automatic_ownership_stale_present_resource_never_registers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
    dry_run: bool,
) -> None:
    path = write_policy(tmp_path, "manager" if owner == "refused" else owner)
    if owner == "refused":
        path.write_text("{}", encoding="utf-8")
    before = path.read_bytes()

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("ownership refusal must not query, register, or retire a scheduler")

    monkeypatch.setattr(fleet_update, "query_task_state", unexpected)
    monkeypatch.setattr(fleet_update, "reconcile_scheduled_task", unexpected)
    result = FleetUpdateResourceHandler().apply(
        ResolvedResource("fleet-update", "sweep", ("fleet-update", "sweep"), {"state": "present"}),
        ResourceContext(tmp_path, {}, "linux", runner=unexpected),
        dry_run,
    )
    assert not result.ok
    assert not result.changed
    assert result.dry_run is dry_run
    assert result.status == ("error" if owner == "refused" else "blocked")
    assert path.read_bytes() == before


@pytest.mark.parametrize("owner", ["manager", "external", "disabled", "refused"])
def test_automatic_ownership_direct_fleet_reconcile_cannot_resurrect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
) -> None:
    path = write_policy(tmp_path, "manager" if owner == "refused" else owner)
    if owner == "refused":
        path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        fleet_update,
        "_reconcile_scheduled_task",
        lambda *args, **kwargs: pytest.fail("no legacy scheduler mutation allowed"),
    )
    result = fleet_update.reconcile_scheduled_task("sweep", desired_present=True, home=tmp_path)
    assert not result.ok
    assert not result.changed
    assert "nothing was changed" in result.detail.lower()


def test_automatic_ownership_cli_install_and_run_enforce_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_policy(tmp_path)
    resource = ResolvedResource("fleet-update", "sweep", ("fleet-update", "sweep"), {})
    monkeypatch.setattr(
        cli_fleet_update,
        "_resolve_fleet_update_resources",
        lambda args: (None, "example", {"sweep": resource}),
    )
    args = argparse.Namespace(tier=["sweep"], json=True)
    assert cli_fleet_update.cmd_install(args) == 2
    assert not json.loads(capsys.readouterr().out)["ok"]
    args.tier = "sweep"
    assert cli_fleet_update.cmd_run(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "deferred"


def test_automatic_ownership_generated_fleet_trigger_identified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "linux")
    unit = fleet_update_tasks.render_linux_service_unit("sweep", home=tmp_path)
    assert "fleet-update run --tier sweep" in unit
    assert "--automatic-trigger" not in unit
    monkeypatch.setattr(fleet_update_tasks.sys, "platform", "win32")
    args = fleet_update_tasks.task_action_arguments("sweep", home=tmp_path)
    assert "fleet-update run --tier sweep" in args
    assert "--automatic-trigger" not in args


@pytest.mark.parametrize("active", ["active", "activating", "deactivating", "inactive"])
def test_automatic_ownership_inventory_reports_old_trigger_and_inflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    active: str,
) -> None:
    monkeypatch.setattr(legacy_update_inventory.sys, "platform", "linux")
    units = tmp_path / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    service = units / "agent-machines-fleet-update-sweep.service"
    service.write_text(
        next(
            body
            for body in historical_linux_bodies(fleet_update_tasks, home=tmp_path)
            if "Environment=" in body and "--automatic-trigger" not in body
        ),
        encoding="utf-8",
    )
    timer = units / "agent-machines-fleet-update-sweep.timer"
    timer.write_text(fleet_update_tasks.render_linux_timer_unit("sweep"), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in units.iterdir()}

    def runner(argv: list[str], **kwargs: Any) -> fleet_update.CommandResult:
        if argv[2] == "is-system-running":
            value = "running"
        elif argv[2] == "is-enabled":
            value = "enabled"
        elif argv[2] == "is-active":
            value = active if argv[-1].endswith(".service") else "active"
        elif argv[2] == "show":
            unit = argv[3]
            loaded = (units / unit).exists()
            value = unit_properties(
                unit,
                units,
                load="loaded" if loaded else "not-found",
                active=active if unit.endswith(".service") and loaded else None,
            )
        else:
            pytest.fail(f"inventory attempted a mutation: {argv}")
        return fleet_update.CommandResult(argv, 0, value)

    monkeypatch.setattr(fleet_update, "shutil_which", lambda binary: binary)
    monkeypatch.setattr(self_update, "shutil_which", lambda binary: binary)
    result = legacy_update_inventory.inventory_legacy_updates(home=tmp_path, runner=runner)[0]
    assert result.preflag_trigger is True
    assert result.retirement_required
    assert result.in_flight is (active != "inactive")
    assert result.blocks_adoption
    assert before == {path.name: path.read_bytes() for path in units.iterdir()}


def test_automatic_ownership_inventory_unavailable_is_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(legacy_update_inventory.sys, "platform", "linux")
    monkeypatch.setattr(fleet_update, "shutil_which", lambda binary: None)
    monkeypatch.setattr(self_update, "shutil_which", lambda binary: None)
    results = legacy_update_inventory.inventory_legacy_updates(home=tmp_path)
    assert all(item.blocks_adoption and item.in_flight is None for item in results)


def test_automatic_ownership_inventory_finds_manual_inflight_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_machines import self_update_lock

    monkeypatch.setattr(legacy_update_inventory.sys, "platform", "linux")
    monkeypatch.setattr(fleet_update, "shutil_which", lambda binary: None)
    monkeypatch.setattr(self_update, "shutil_which", lambda binary: None)
    monkeypatch.setattr(self_update_lock, "_pid_alive", lambda pid: True)
    path = self_update_lock.lock_path("sweep", tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": 123, "started_at": "2026-01-01T00:00:00+00:00"}))
    result = legacy_update_inventory.inventory_legacy_updates(home=tmp_path)[1]
    assert result.in_flight is True
    assert result.lock_owner_pid == 123
    assert result.blocks_adoption


@pytest.mark.parametrize("state", ["Running", "Queued", "Ready", None])
def test_automatic_ownership_windows_inventory_preserves_uncertainty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str | None,
) -> None:
    monkeypatch.setattr(legacy_update_inventory.sys, "platform", "win32")
    for tasks in (fleet_update_tasks, self_update_tasks):

        def snapshot(*args: Any, _tasks: Any = tasks, **kwargs: Any) -> Any:
            arguments = (
                historical_windows_arguments(_tasks, home=tmp_path)[0]
                if _tasks is fleet_update_tasks
                else _tasks.task_action_arguments("sweep", home=tmp_path)
            )
            return fleet_update_tasks.ScheduledTaskSnapshot(
                task_name=_tasks.TIER_SPECS["sweep"].task_name,
                present=True,
                enabled=True,
                state=state,
                arguments=arguments,
                task_path="\\",
                action_count=1,
                trigger_count=1,
                run_level="Limited",
                logon_type="Interactive",
                description=_tasks.task_description("sweep"),
                execute="conhost.exe",
                working_directory=_tasks.task_working_directory(tmp_path),
                trigger_kind="daily",
                trigger_value=1,
            )

        monkeypatch.setattr(
            tasks,
            "query_scheduled_task",
            snapshot,
        )
    result = legacy_update_inventory.inventory_legacy_updates(home=tmp_path)[0]
    assert result.blocks_adoption
    if state is None:
        assert result.in_flight is None
        assert "refused" in result.detail
    else:
        assert result.preflag_trigger is True
        assert result.in_flight is (state in {"Running", "Queued"})


def test_automatic_ownership_invalid_policy_status_is_loud(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_policy(tmp_path).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        cli_fleet_update,
        "_resolve_fleet_update_resources",
        lambda args: (None, "example", {}),
    )
    args = argparse.Namespace(tier=[], json=True, legacy_inventory=True)
    monkeypatch.setattr(cli_fleet_update, "inventory_legacy_updates", lambda: [])
    monkeypatch.setattr(
        fleet_update,
        "scheduled_task_status",
        lambda *args, **kwargs: fleet_update.ScheduledTaskStatus(
            "sweep",
            False,
            "example",
            False,
        ),
    )
    assert cli_fleet_update.cmd_status(args) == 2
    payload = json.loads(capsys.readouterr().out)
    assert not payload["ok"]
    assert not payload["adoption_ready"]
    assert payload["ownership"]["state"] == "refused"


@pytest.mark.parametrize("tasks", [self_update_tasks, fleet_update_tasks])
def test_automatic_ownership_failed_retirement_keeps_unit_evidence(
    tmp_path: Path,
    tasks: Any,
) -> None:
    units = tmp_path / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    name = tasks.TIER_SPECS["sweep"].task_name
    paths = [units / f"{name}.{suffix}" for suffix in ("timer", "service")]
    paths[0].write_text(tasks.render_linux_timer_unit("sweep"), encoding="utf-8")
    paths[1].write_text(tasks.render_linux_service_unit("sweep", home=tmp_path), encoding="utf-8")
    before = {path: path.read_bytes() for path in paths}
    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: Any) -> fleet_update.CommandResult:
        calls.append(argv)
        if argv[2] == "show":
            return fleet_update.CommandResult(argv, 0, unit_properties(argv[3], units))
        return fleet_update.CommandResult(argv, 1, "", "permission denied")

    result = tasks.unregister_systemd_timer(
        "sweep",
        runner=runner,
        resolve_binary=lambda name: name,
        home=tmp_path,
    )
    assert result.returncode == 1
    assert all(path.read_bytes() == before[path] for path in paths)
    assert sum(call[2] == "disable" for call in calls) == 1
