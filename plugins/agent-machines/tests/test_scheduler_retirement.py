"""Retirement validates exact ownership and never stops an active installer."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_machines import fleet_update, fleet_update_tasks, legacy_update_inventory
from agent_machines.legacy_fleet_definitions import (
    historical_linux_bodies,
    historical_windows_arguments,
)
from agent_machines.scheduler_retirement import (
    RetirementBusy,
    RetirementRefused,
    query_unit_activity,
)

from ._scheduler_safety_helpers import unit_properties


@pytest.fixture(autouse=True)
def isolated_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def windows_payload(home: Path, **overrides: Any) -> dict[str, Any]:
    return {
        "present": True,
        "enabled": True,
        "state": "Ready",
        "task_path": "\\",
        "action_count": 1,
        "trigger_count": 1,
        "run_level": "Limited",
        "logon_type": "Interactive",
        "description": fleet_update_tasks.task_description("sweep"),
        "execute": "conhost.exe",
        "arguments": fleet_update_tasks.task_action_arguments("sweep", home=home),
        "working_directory": fleet_update_tasks.task_working_directory(home),
        "trigger_kind": "daily",
        "trigger_value": 1,
        **overrides,
    }


def windows_runner(
    payload: dict[str, Any],
    calls: list[str],
    *,
    failure: str | None = None,
) -> Any:
    def runner(argv: list[str], **kwargs: Any) -> fleet_update.CommandResult:
        script = argv[-1]
        calls.append(script)
        if "ConvertTo-Json" in script:
            return fleet_update.CommandResult(argv, 0, json.dumps(payload))
        assert "Unregister-ScheduledTask" in script
        return fleet_update.CommandResult(argv, 1 if failure else 0, "", failure or "")

    return runner


def use_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fleet_update, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(fleet_update_tasks, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(fleet_update, "shutil_which", lambda binary: binary)


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "Running"},
        {"state": "Queued"},
        {"state": None},
        {"arguments": "cmd.exe /c foreign.exe"},
        {"execute": "foreign.exe"},
        {"action_count": 2},
        {"task_path": "\\Foreign\\"},
        {"run_level": "Highest"},
        {"present": "false"},
        {"enabled": None},
    ],
)
def test_scheduler_retirement_windows_blocks_unproved_or_busy_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, Any],
) -> None:
    use_windows(monkeypatch)
    calls: list[str] = []
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=False,
        home=tmp_path,
        runner=windows_runner(windows_payload(tmp_path, **changes), calls),
    )
    assert not result.ok
    assert not result.changed
    if changes.get("state") in {"Running", "Queued"}:
        assert result.status == "deferred"
    assert not any(
        "Unregister-ScheduledTask" in call or "Stop-ScheduledTask" in call for call in calls
    )


def test_scheduler_retirement_windows_live_lock_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_windows(monkeypatch)
    path = tmp_path / ".agent-machines" / "fleet-update" / "sweep.lock.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    calls: list[str] = []
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=False,
        home=tmp_path,
        runner=windows_runner(windows_payload(tmp_path), calls),
    )
    assert result.status == "deferred"
    assert path.exists()
    assert not any("Unregister-ScheduledTask" in call for call in calls)


@pytest.mark.parametrize("old", [False, True])
def test_scheduler_retirement_windows_exact_owned_definition_can_retire_after_adoption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old: bool,
) -> None:
    use_windows(monkeypatch)
    policy = tmp_path / "manager" / "automatic-updates.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.automatic-updates",
                "version": 1,
                "owner": "manager",
                "provenance": "explicit-user",
                "user_disabled": False,
                "interval_seconds": 3600,
                "project": "example",
                "external_controller": None,
            }
        ),
        encoding="utf-8",
    )
    payload = windows_payload(tmp_path)
    if old:
        payload["arguments"] = historical_windows_arguments(fleet_update_tasks, home=tmp_path)[0]
    calls: list[str] = []
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=False,
        home=tmp_path,
        runner=windows_runner(payload, calls),
    )
    assert result.ok and result.changed
    assert sum("Unregister-ScheduledTask" in call for call in calls) == 1
    assert not any("Stop-ScheduledTask" in call for call in calls)
    assert json.loads(policy.read_text())["owner"] == "manager"


def test_scheduler_retirement_windows_unregister_failure_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_windows(monkeypatch)
    calls: list[str] = []
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=False,
        home=tmp_path,
        runner=windows_runner(windows_payload(tmp_path), calls, failure="access denied"),
    )
    assert result.status == "error"
    assert not result.changed
    assert "access denied" in result.detail


def test_scheduler_retirement_windows_read_failure_never_unregisters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_windows(monkeypatch)
    calls: list[str] = []

    def runner(argv: list[str], **kwargs: Any) -> fleet_update.CommandResult:
        calls.append(argv[-1])
        return fleet_update.CommandResult(argv, 1, "", "query denied")

    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=False,
        home=tmp_path,
        runner=runner,
    )
    assert result.status == "error"
    assert not any("Unregister-ScheduledTask" in call for call in calls)


def test_scheduler_retirement_direct_windows_boundary_preserves_running_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_windows(monkeypatch)
    calls: list[str] = []
    with pytest.raises(RetirementBusy):
        fleet_update_tasks.unregister_scheduled_task(
            "sweep",
            home=tmp_path,
            runner=windows_runner(windows_payload(tmp_path, state="Running"), calls),
            resolve_binary=lambda binary: binary,
        )
    assert not any("Unregister-ScheduledTask" in call for call in calls)


def linux_units(home: Path, *, old: bool = False) -> Path:
    directory = fleet_update_tasks._linux_unit_dir(home)
    directory.mkdir(parents=True)
    name = fleet_update_tasks.TIER_SPECS["sweep"].task_name
    service = fleet_update_tasks.render_linux_service_unit("sweep", home=home)
    if old:
        service = next(
            body for body in historical_linux_bodies(fleet_update_tasks, home=home)
            if "Environment=" in body and "--automatic-trigger" not in body
        )
    (directory / f"{name}.service").write_text(service, encoding="utf-8")
    (directory / f"{name}.timer").write_text(
        fleet_update_tasks.render_linux_timer_unit("sweep"),
        encoding="utf-8",
    )
    return directory


def linux_runner(
    directory: Path,
    calls: list[list[str]],
    *,
    service: dict[str, Any] | None = None,
    malformed: str | None = None,
    disable_failure: bool = False,
) -> Any:
    def runner(argv: list[str], **kwargs: Any) -> fleet_update.CommandResult:
        calls.append(argv)
        verb = argv[2]
        if verb == "is-system-running":
            output = "running"
        elif verb == "is-enabled":
            output = "enabled"
        elif verb == "is-active":
            output = "active"
        elif verb == "show":
            output = (
                malformed
                if malformed is not None
                else unit_properties(
                    argv[3],
                    directory,
                    **(service or {}) if argv[3].endswith(".service") else {},
                )
            )
        elif verb in {"disable", "daemon-reload"}:
            if verb == "disable" and disable_failure:
                return fleet_update.CommandResult(argv, 1, "", "disable denied")
            output = ""
        else:
            pytest.fail(f"unexpected command: {argv}")
        return fleet_update.CommandResult(argv, 0, output)

    return runner


@pytest.mark.parametrize("case", ["running", "queued", "unknown", "empty", "foreign", "drop-in"])
def test_scheduler_retirement_linux_blocks_without_disable_or_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    monkeypatch.setattr(fleet_update, "shutil_which", lambda binary: binary)
    directory = linux_units(tmp_path, old=True)
    if case == "foreign":
        next(directory.glob("*.service")).write_text("ExecStart=/foreign\n", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    calls: list[list[str]] = []
    service = (
        {"active": "active", "sub": "running", "pid": 123}
        if case == "running"
        else {"job": 123}
        if case == "queued"
        else {"drop_ins": "/foreign/override.conf"}
        if case == "drop-in"
        else {}
    )
    malformed = "unknown" if case == "unknown" else "" if case == "empty" else None
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=False,
        home=tmp_path,
        runner=linux_runner(directory, calls, service=service, malformed=malformed),
    )
    assert not result.ok and not result.changed
    if case in {"running", "queued"}:
        assert result.status == "deferred"
    assert not any(call[2] in {"disable", "daemon-reload"} for call in calls)
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}


@pytest.mark.parametrize("old", [False, True])
def test_scheduler_retirement_linux_exact_owned_idle_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old: bool,
) -> None:
    monkeypatch.setattr(fleet_update, "shutil_which", lambda binary: binary)
    directory = linux_units(tmp_path, old=old)
    calls: list[list[str]] = []
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=False,
        home=tmp_path,
        runner=linux_runner(directory, calls),
    )
    assert result.ok and result.changed, result.detail
    assert not list(directory.iterdir())
    assert any(call[2] == "disable" for call in calls)


def test_scheduler_retirement_linux_failed_disable_retains_owned_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fleet_update, "shutil_which", lambda binary: binary)
    directory = linux_units(tmp_path, old=True)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    calls: list[list[str]] = []
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=False,
        home=tmp_path,
        runner=linux_runner(directory, calls, disable_failure=True),
    )
    assert result.status == "error"
    assert "disable denied" in result.detail
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}


def test_scheduler_retirement_direct_linux_boundary_preserves_queued_job(
    tmp_path: Path,
) -> None:
    directory = linux_units(tmp_path, old=True)
    calls: list[list[str]] = []
    with pytest.raises(RetirementBusy):
        fleet_update_tasks.unregister_systemd_timer(
            "sweep",
            home=tmp_path,
            runner=linux_runner(directory, calls, service={"job": 123}),
            resolve_binary=lambda binary: binary,
        )
    assert not any(call[2] == "disable" for call in calls)
    assert list(directory.iterdir())


@pytest.mark.parametrize("load", ["loaded", "not-found"])
def test_scheduler_retirement_unknown_is_never_inactive_proof(
    tmp_path: Path,
    load: str,
) -> None:
    def runner(argv: list[str], **kwargs: Any) -> fleet_update.CommandResult:
        return fleet_update.CommandResult(
            argv,
            0,
            unit_properties(argv[3], tmp_path, load=load, active="unknown"),
        )

    with pytest.raises(RetirementRefused, match="unknown"):
        query_unit_activity("example.service", runner=runner)


def test_scheduler_retirement_not_found_properties_prove_absent_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fleet_update, "shutil_which", lambda binary: binary)
    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: Any) -> fleet_update.CommandResult:
        calls.append(argv)
        if argv[2] == "is-system-running":
            return fleet_update.CommandResult(argv, 0, "running")
        assert argv[2] == "show"
        return fleet_update.CommandResult(
            argv,
            1,
            unit_properties(argv[3], tmp_path, load="not-found"),
        )

    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=False,
        home=tmp_path,
        runner=runner,
    )
    assert result.ok and not result.changed, result.detail
    assert not any(call[2] in {"disable", "daemon-reload"} for call in calls)


def test_scheduler_retirement_malformed_lock_is_a_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fleet_update, "shutil_which", lambda binary: binary)
    directory = linux_units(tmp_path)
    lock = tmp_path / ".agent-machines" / "fleet-update" / "sweep.lock.json"
    lock.parent.mkdir(parents=True)
    lock.write_text("malformed", encoding="utf-8")
    calls: list[list[str]] = []
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=False,
        home=tmp_path,
        runner=linux_runner(directory, calls),
    )
    assert result.status == "error"
    assert not any(call[2] == "disable" for call in calls)
    assert list(directory.iterdir())


def test_scheduler_retirement_xdg_gap_is_an_observable_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = linux_units(tmp_path)
    xdg = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setattr(fleet_update, "shutil_which", lambda binary: binary)
    calls: list[list[str]] = []
    runner = linux_runner(directory, calls)
    artifacts = legacy_update_inventory.inventory_legacy_updates(home=tmp_path, runner=runner)
    assert artifacts[0].blocks_adoption
    assert artifacts[0].in_flight is None
    assert "XDG_CONFIG_HOME" in artifacts[0].detail
    with pytest.raises(RetirementRefused, match="XDG_CONFIG_HOME"):
        fleet_update_tasks.unregister_systemd_timer(
            "sweep",
            home=tmp_path,
            runner=runner,
            resolve_binary=lambda binary: binary,
        )
    assert list(directory.iterdir())
    assert not any(call[2] == "disable" for call in calls)
