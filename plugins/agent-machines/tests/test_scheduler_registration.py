"""Reconfiguration is an owned, idle mutation, not an unconditional force-heal."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_machines import fleet_update, fleet_update_tasks, self_update_tasks
from agent_machines.legacy_fleet_definitions import historical_windows_arguments
from agent_machines.scheduler_retirement import RetirementBusy, RetirementRefused

from ._manager_compatibility_helpers import default_policy
from ._scheduler_safety_helpers import fixture_unit_properties
from .test_scheduler_retirement import linux_units, use_windows, windows_payload


@pytest.fixture(autouse=True)
def isolated_registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def windows_registration_runner(
    home: Path,
    initial: dict[str, Any],
    calls: list[str],
    *,
    registered: dict[str, Any] | None = None,
) -> Any:
    state = dict(initial)

    def runner(argv: list[str], **kwargs: Any) -> fleet_update.CommandResult:
        script = argv[-1]
        calls.append(script)
        if "ConvertTo-Json" in script:
            return fleet_update.CommandResult(argv, 0, json.dumps(state))
        assert "Register-ScheduledTask" in script
        assert "Stop-ScheduledTask" not in script
        assert "Unregister-ScheduledTask" not in script
        assert "if ($matches.Count -eq 0) { exit 0 }" not in script
        state.clear()
        state.update(windows_payload(home) if registered is None else registered)
        return fleet_update.CommandResult(argv, 0)

    return runner


@pytest.mark.parametrize("old", [False, True])
def test_scheduler_registration_windows_absent_or_owned_inactive_old_allowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old: bool,
) -> None:
    use_windows(monkeypatch)
    initial = (
        windows_payload(
            tmp_path,
            arguments=historical_windows_arguments(fleet_update_tasks, home=tmp_path)[0],
        )
        if old
        else {"present": False}
    )
    calls: list[str] = []
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=True,
        home=tmp_path,
        runner=windows_registration_runner(tmp_path, initial, calls),
    )
    assert result.ok and result.changed, result.detail
    assert result.snapshot is not None and result.snapshot.matching
    assert sum("Register-ScheduledTask" in script for script in calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"state": "Running"},
        {"state": "Queued"},
        {"state": None},
        {"arguments": "cmd.exe /c foreign.exe"},
        {"action_count": 2},
    ],
)
def test_scheduler_registration_windows_foreign_busy_unknown_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, Any],
) -> None:
    use_windows(monkeypatch)
    initial = windows_payload(
        tmp_path,
        arguments=historical_windows_arguments(fleet_update_tasks, home=tmp_path)[0],
    )
    initial.update(change)
    calls: list[str] = []
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=True,
        home=tmp_path,
        runner=windows_registration_runner(tmp_path, initial, calls),
    )
    assert not result.ok and not result.changed
    assert not any("Register-ScheduledTask" in script for script in calls)


def test_scheduler_registration_direct_windows_boundary_refuses_running_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_windows(monkeypatch)
    calls: list[str] = []
    with pytest.raises(RetirementBusy):
        fleet_update_tasks.register_scheduled_task(
            "sweep",
            home=tmp_path,
            resolve_binary=lambda name: name,
            runner=windows_registration_runner(
                tmp_path,
                windows_payload(tmp_path, state="Running"),
                calls,
            ),
        )
    assert not any("Register-ScheduledTask" in script for script in calls)


def linux_registration_runner(
    directory: Path,
    calls: list[list[str]],
    *,
    case: str | None = None,
) -> Any:
    def runner(argv: list[str], **kwargs: Any) -> fleet_update.CommandResult:
        calls.append(argv)
        verb = argv[2]
        if verb == "is-system-running":
            value = "running"
        elif verb == "is-enabled":
            value = "enabled"
        elif verb == "is-active":
            value = "active"
        elif verb == "show":
            from ._scheduler_safety_helpers import unit_properties

            if argv[3].endswith(".service") and case == "running":
                value = unit_properties(
                    argv[3], directory, active="active", sub="running", pid=123
                )
            elif argv[3].endswith(".service") and case == "queued":
                value = unit_properties(argv[3], directory, job=123)
            elif case == "unknown":
                value = ""
            else:
                value = fixture_unit_properties(argv[3], directory)
        elif verb in {"daemon-reload", "enable"}:
            value = ""
        else:
            pytest.fail(f"unexpected or destructive registration command: {argv}")
        return fleet_update.CommandResult(argv, 0, value)

    return runner


@pytest.mark.parametrize("old", [False, True])
def test_scheduler_registration_linux_not_found_or_owned_inactive_old_allowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old: bool,
) -> None:
    monkeypatch.setattr(fleet_update, "shutil_which", lambda name: name)
    directory = (
        linux_units(tmp_path, old=True) if old else fleet_update_tasks._linux_unit_dir(tmp_path)
    )
    calls: list[list[str]] = []
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=True,
        home=tmp_path,
        runner=linux_registration_runner(directory, calls),
    )
    assert result.ok and result.changed, result.detail
    assert result.snapshot is not None and result.snapshot.matching
    service = directory / "agent-machines-fleet-update-sweep.service"
    assert "fleet-update run --tier sweep" in service.read_text(encoding="utf-8")
    assert sum(call[2] == "daemon-reload" for call in calls) == 1


@pytest.mark.parametrize("case", ["running", "queued", "foreign", "edited", "unknown"])
def test_scheduler_registration_linux_unsafe_existing_artifacts_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    monkeypatch.setattr(fleet_update, "shutil_which", lambda name: name)
    directory = linux_units(tmp_path, old=True)
    service = directory / "agent-machines-fleet-update-sweep.service"
    if case == "foreign":
        service.write_text("ExecStart=/foreign\n", encoding="utf-8")
    elif case == "edited":
        service.write_text(
            service.read_text(encoding="utf-8") + "# local edit\n", encoding="utf-8"
        )
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    calls: list[list[str]] = []
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=True,
        home=tmp_path,
        runner=linux_registration_runner(directory, calls, case=case),
    )
    assert not result.ok and not result.changed
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}
    assert not any(call[2] in {"daemon-reload", "enable"} for call in calls)


def test_scheduler_registration_direct_linux_boundary_refuses_edited_body(
    tmp_path: Path,
) -> None:
    directory = linux_units(tmp_path, old=True)
    service = directory / "agent-machines-fleet-update-sweep.service"
    service.write_text("ExecStart=/foreign\n", encoding="utf-8")
    calls: list[list[str]] = []
    with pytest.raises(RetirementRefused):
        fleet_update_tasks.register_systemd_timer(
            "sweep",
            home=tmp_path,
            resolve_binary=lambda name: name,
            runner=linux_registration_runner(directory, calls),
        )
    assert service.read_text(encoding="utf-8") == "ExecStart=/foreign\n"
    assert calls == []


@pytest.mark.parametrize("owner", ["manager", "external", "disabled"])
def test_scheduler_registration_owner_policy_still_blocks_fresh_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
) -> None:
    policy = tmp_path / "manager" / "automatic-updates.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        json.dumps(
            {
                **default_policy(),
                "owner": owner,
                "project": "example" if owner == "manager" else None,
                "external_controller": "example" if owner == "external" else None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fleet_update, "shutil_which", lambda name: name)
    calls: list[list[str]] = []
    result = fleet_update.reconcile_scheduled_task(
        "sweep",
        desired_present=True,
        home=tmp_path,
        runner=linux_registration_runner(fleet_update_tasks._linux_unit_dir(tmp_path), calls),
    )
    assert not result.ok and not result.changed
    assert calls == []


def self_windows_payload(
    home: Path, machine: str | None, **overrides: Any,
) -> dict[str, Any]:
    return windows_payload(
        home,
        description=self_update_tasks.task_description("sweep"),
        arguments=self_update_tasks.task_action_arguments("sweep", home=home, machine=machine),
        working_directory=self_update_tasks.task_working_directory(home),
        **overrides,
    )


@pytest.mark.parametrize("old,new", [("old-box", "new-box"), ("old-box", None), (None, "new-box")])
@pytest.mark.parametrize("direct", [False, True])
def test_self_registration_windows_rebinds_exact_owned_inactive_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old: str | None,
    new: str | None,
    direct: bool,
) -> None:
    use_windows(monkeypatch)
    monkeypatch.setattr(self_update_tasks, "sys", SimpleNamespace(platform="win32"))
    initial = self_windows_payload(tmp_path, old)
    calls: list[str] = []
    runner = windows_registration_runner(
        tmp_path, initial, calls, registered=self_windows_payload(tmp_path, new),
    )
    if direct:
        result = self_update_tasks.register_scheduled_task(
            "sweep", home=tmp_path, machine=new, runner=runner, resolve_binary=lambda name: name,
        )
        assert result.returncode == 0
    else:
        result = self_update_tasks.reconcile_scheduled_task(
            "sweep", desired_present=True, home=tmp_path, machine=new, runner=runner,
            resolve_binary=lambda name: name, record_task_config=lambda *args, **kwargs: None,
        )
        assert result.ok and result.changed, result.detail
        assert result.snapshot is not None and result.snapshot.matching
    scripts = [script for script in calls if "Register-ScheduledTask" in script]
    assert len(scripts) == 1
    guard, action = scripts[0].split("$action = New-ScheduledTaskAction", 1)
    old_arguments = self_update_tasks._ps_literal(initial["arguments"])
    new_arguments = self_update_tasks._ps_literal(
        self_update_tasks.task_action_arguments("sweep", home=tmp_path, machine=new),
    )
    assert f"-notin @({old_arguments})" in guard
    assert new_arguments not in guard
    assert f"-Argument {new_arguments}" in action
    assert "'Ready', 'Disabled'" in guard


@pytest.mark.parametrize(
    "change",
    [
        {"state": "Running"},
        {"state": "Queued"},
        {"state": None},
        {"enabled": None},
        {"execute": "foreign.exe"},
        {"arguments": "cmd.exe /c foreign.exe --machine old-box"},
        {"action_count": 2},
        {"task_path": "\\Foreign\\"},
    ],
)
@pytest.mark.parametrize("direct", [False, True])
def test_self_registration_windows_rebind_preserves_unsafe_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, Any],
    direct: bool,
) -> None:
    use_windows(monkeypatch)
    monkeypatch.setattr(self_update_tasks, "sys", SimpleNamespace(platform="win32"))
    initial = self_windows_payload(tmp_path, "old-box")
    initial.update(change)
    calls: list[str] = []
    runner = windows_registration_runner(tmp_path, initial, calls)
    kwargs = dict(
        home=tmp_path, machine="new-box", runner=runner, resolve_binary=lambda name: name,
    )
    if direct:
        with pytest.raises(RetirementRefused):
            self_update_tasks.register_scheduled_task("sweep", **kwargs)
    else:
        result = self_update_tasks.reconcile_scheduled_task(
            "sweep", desired_present=True, **kwargs,
            record_task_config=lambda *args, **kwargs: None,
        )
        assert not result.ok and not result.changed
    assert not any("Register-ScheduledTask" in script for script in calls)


def self_linux_units(home: Path, machine: str | None) -> Path:
    directory = self_update_tasks._linux_unit_dir(home)
    directory.mkdir(parents=True)
    name = self_update_tasks.TIER_SPECS["sweep"].task_name
    (directory / f"{name}.service").write_text(
        self_update_tasks.render_linux_service_unit("sweep", home=home, machine=machine),
        encoding="utf-8",
    )
    (directory / f"{name}.timer").write_text(
        self_update_tasks.render_linux_timer_unit("sweep"), encoding="utf-8",
    )
    return directory


@pytest.mark.parametrize("old,new", [("old-box", "new-box"), ("old-box", None), (None, "new-box")])
@pytest.mark.parametrize("direct", [False, True])
def test_self_registration_linux_rebinds_exact_owned_inactive_definition(
    tmp_path: Path,
    old: str | None,
    new: str | None,
    direct: bool,
) -> None:
    directory = self_linux_units(tmp_path, old)
    calls: list[list[str]] = []
    kwargs = dict(
        home=tmp_path, machine=new, runner=linux_registration_runner(directory, calls),
        resolve_binary=lambda name: name,
    )
    if direct:
        result = self_update_tasks.register_systemd_timer("sweep", **kwargs)
        assert result.returncode == 0
    else:
        result = self_update_tasks.reconcile_scheduled_task(
            "sweep", desired_present=True, **kwargs,
            record_task_config=lambda *args, **kwargs: None,
        )
        assert result.ok and result.changed, result.detail
        assert result.snapshot is not None and result.snapshot.matching
    assert self_update_tasks._linux_service_unit_path("sweep", tmp_path).read_text(
        encoding="utf-8",
    ) == self_update_tasks.render_linux_service_unit("sweep", home=tmp_path, machine=new)
    assert sum(call[2] == "daemon-reload" for call in calls) == 1


@pytest.mark.parametrize("case", ["running", "queued", "foreign", "edited", "unknown"])
@pytest.mark.parametrize("direct", [False, True])
def test_self_registration_linux_rebind_preserves_unsafe_definition(
    tmp_path: Path,
    case: str,
    direct: bool,
) -> None:
    directory = self_linux_units(tmp_path, "old-box")
    service = self_update_tasks._linux_service_unit_path("sweep", tmp_path)
    if case == "foreign":
        service.write_text("ExecStart=/foreign --machine old-box\n", encoding="utf-8")
    elif case == "edited":
        service.write_text(service.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    calls: list[list[str]] = []
    kwargs = dict(
        home=tmp_path, machine="new-box",
        runner=linux_registration_runner(directory, calls, case=case),
        resolve_binary=lambda name: name,
    )
    if direct:
        with pytest.raises(RetirementRefused):
            self_update_tasks.register_systemd_timer("sweep", **kwargs)
    else:
        result = self_update_tasks.reconcile_scheduled_task(
            "sweep", desired_present=True, **kwargs,
            record_task_config=lambda *args, **kwargs: None,
        )
        assert not result.ok and not result.changed
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}
    assert not any(call[2] in {"daemon-reload", "enable"} for call in calls)
