"""Fail-closed ownership and inactivity proofs for explicit legacy retirement."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .automatic_update_ownership import read_update_ownership
from .legacy_fleet_definitions import historical_linux_bodies, historical_windows_arguments


class RetirementRefused(RuntimeError):
    """Ownership or inactivity cannot be proved."""


class RetirementBusy(RetirementRefused):
    """An installer or queued invocation must be preserved."""


@dataclass(frozen=True)
class UnitActivity:
    load_state: str
    active_state: str
    sub_state: str
    job: int
    main_pid: int
    fragment_path: str
    drop_ins: str

    @property
    def in_flight(self) -> bool:
        return (
            self.job != 0
            or self.main_pid != 0
            or self.active_state in {"active", "activating", "reloading", "deactivating"}
        )


def query_unit_activity(unit: str, *, runner: Callable[..., Any]) -> UnitActivity:
    properties = ["LoadState", "ActiveState", "SubState", "Job", "FragmentPath", "DropInPaths"]
    service = unit.endswith(".service")
    if service:
        properties.append("MainPID")
    result = runner(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            *[f"--property={name}" for name in properties],
        ],
        timeout=30,
    )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in properties or key in values:
            raise RetirementRefused(f"invalid unit activity output for {unit}")
        values[key] = value.strip()
    if set(values) != set(properties):
        raise RetirementRefused(f"incomplete unit activity output for {unit}")
    load = values["LoadState"]
    active = values["ActiveState"]
    sub = values["SubState"]
    if load not in {"loaded", "not-found"} or active not in {
        "active",
        "inactive",
        "failed",
        "activating",
        "reloading",
        "deactivating",
    }:
        raise RetirementRefused(f"unknown unit activity for {unit}: {load}/{active}/{sub}")
    if result.returncode != 0 and load != "not-found":
        raise RetirementRefused(result.output or f"unit activity query failed for {unit}")
    if not values["Job"].isdigit() or (service and not values["MainPID"].isdigit()):
        raise RetirementRefused(f"unverifiable unit job/process state for {unit}")
    activity = UnitActivity(
        load,
        active,
        sub,
        int(values["Job"]),
        int(values["MainPID"]) if service else 0,
        values["FragmentPath"],
        values["DropInPaths"],
    )
    if load == "not-found" and (
        active != "inactive"
        or sub != "dead"
        or activity.job
        or activity.main_pid
        or activity.fragment_path
        or activity.drop_ins
    ):
        raise RetirementRefused(f"inconsistent not-found unit state for {unit}")
    if active == "inactive" and sub != "dead":
        raise RetirementRefused(f"unknown inactive unit substate for {unit}: {sub}")
    if active == "failed" and sub != "failed":
        raise RetirementRefused(f"unknown failed unit substate for {unit}: {sub}")
    return activity


def linux_unit_directories(tasks: Any, home: Path | None) -> tuple[Path, ...]:
    legacy = tasks._linux_unit_dir(home)
    configured = os.environ.get("XDG_CONFIG_HOME")
    if not configured:
        return (legacy,)
    xdg = Path(configured)
    if not xdg.is_absolute():
        raise RetirementRefused("XDG_CONFIG_HOME is not absolute; unit paths cannot be verified")
    native = xdg / "systemd" / "user"
    return (legacy,) if native == legacy else (legacy, native)


def live_tier_lock_owner(kind: str, tier: str, home: Path | None) -> int | None:
    from . import fleet_update_lock, self_update_lock

    locks = fleet_update_lock if kind == "fleet" else self_update_lock
    path = locks.lock_path(tier, home)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        if path.is_symlink():
            raise RetirementRefused(f"legacy update lock is dangling: {path}") from exc
        return None
    except (OSError, ValueError, UnicodeError) as exc:
        raise RetirementRefused(f"legacy update lock is unreadable: {path}: {exc}") from exc
    pid = record.get("pid") if isinstance(record, dict) else None
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        raise RetirementRefused(f"legacy update lock owner cannot be verified: {path}")
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return pid
        error = ctypes.windll.kernel32.GetLastError()
        if error != 87:  # ERROR_INVALID_PARAMETER means the process no longer exists.
            raise RetirementRefused(f"legacy lock process query failed with Windows error {error}")
        return None
    return pid if locks._pid_alive(pid) else None


def definition_machine(text: str) -> str | None:
    match = re.search(r" --machine ([A-Za-z0-9][A-Za-z0-9_.:-]*)$", text, re.MULTILINE)
    return match.group(1) if match else None


def owned_windows_arguments(
    kind: str,
    tasks: Any,
    tier: str,
    *,
    home: Path | None,
    machine: str | None = None,
) -> tuple[str, ...]:
    current = (
        tasks.task_action_arguments(tier, home=home, machine=machine)
        if kind == "self"
        else tasks.task_action_arguments(tier, home=home)
    )
    if kind != "fleet":
        return (current,)
    return (current, *historical_windows_arguments(tasks, home=home))


def windows_task_snapshot(
    snapshot_type: Callable[..., Any],
    task_name: str,
    payload: dict[str, Any],
) -> Any:
    if type(payload.get("present")) is not bool:
        raise RetirementRefused("Scheduled Task query returned no boolean presence evidence")
    strings = {
        name: payload.get(name) if isinstance(payload.get(name), str) else None
        for name in (
            "state",
            "logon_type",
            "description",
            "execute",
            "arguments",
            "working_directory",
            "trigger_kind",
            "task_path",
            "run_level",
        )
    }
    numbers = {
        name: payload.get(name) if type(payload.get(name)) is int else None
        for name in ("trigger_value", "action_count", "trigger_count")
    }
    return snapshot_type(
        task_name=task_name,
        present=payload["present"],
        enabled=payload.get("enabled") if type(payload.get("enabled")) is bool else None,
        **strings,
        **numbers,
    )


def require_registration_policy(kind: str, home: Path | None) -> None:
    if kind == "fleet":
        ownership = read_update_ownership(home)
        if ownership.blocks_legacy_schedule:
            raise RetirementRefused(ownership.detail + "; no legacy fleet registration")


def windows_mutation_guard_script(
    kind: str,
    tasks: Any,
    tier: str,
    *,
    home: Path | None,
    machine: str | None = None,
) -> str:
    literal = tasks._ps_literal
    task_name = tasks.TIER_SPECS[tier].task_name
    accepted = ", ".join(
        literal(value)
        for value in owned_windows_arguments(kind, tasks, tier, home=home, machine=machine)
    )
    return f"""
$ErrorActionPreference = 'Stop'
$matches = @(Get-ScheduledTask -ErrorAction Stop | Where-Object {{
  $_.TaskName -eq {literal(task_name)}
}})
if ($matches.Count -gt 1) {{ throw 'multiple same-name tasks; ownership is ambiguous' }}
if ($matches.Count -gt 0) {{
$task = $matches[0]
if ([string]$task.State -notin @('Ready', 'Disabled')) {{
  throw 'task is active, queued or unknown; no scheduler mutation'
}}
if (@($task.Actions).Count -ne 1 -or @($task.Triggers).Count -ne 1 -or
    [string]$task.TaskPath -ne '\\' -or
    [string]$task.Principal.LogonType -ne 'Interactive' -or
    [string]$task.Principal.RunLevel -ne 'Limited' -or
    [string]$task.Description -ne {literal(tasks.task_description(tier))} -or
    [string]$task.Actions[0].Execute -ne 'conhost.exe' -or
    [string]$task.Actions[0].Arguments -notin @({accepted}) -or
    [string]$task.Actions[0].WorkingDirectory -ne {literal(tasks.task_working_directory(home))}) {{
  throw 'same-name task definition changed; no scheduler mutation'
}}
}}
""".strip()


def windows_retirement_script(
    kind: str,
    tasks: Any,
    tier: str,
    *,
    home: Path | None,
    machine: str | None = None,
) -> str:
    guard = windows_mutation_guard_script(kind, tasks, tier, home=home, machine=machine)
    return (
        guard
        + "\n"
        + f"""
if ($matches.Count -eq 0) {{ exit 0 }}
Unregister-ScheduledTask `
  -TaskName {tasks._ps_literal(tasks.TIER_SPECS[tier].task_name)} `
  -TaskPath '\\' `
  -Confirm:$false `
  -ErrorAction Stop | Out-Null
""".strip()
    )


def windows_definition_owned(
    kind: str,
    tasks: Any,
    snapshot: Any,
    tier: str,
    *,
    home: Path | None,
    machine: str | None = None,
) -> bool:
    if not snapshot.present:
        return True
    spec = tasks.TIER_SPECS[tier]
    normalize = tasks._normalize_windows_text
    arguments = (snapshot.arguments or "").casefold()
    if kind == "self" and machine is None:
        machine = definition_machine(arguments)
    return (
        snapshot.task_name == spec.task_name
        and snapshot.task_path == "\\"
        and snapshot.action_count == snapshot.trigger_count == 1
        and (snapshot.execute or "").casefold() == "conhost.exe"
        and snapshot.logon_type == "Interactive"
        and snapshot.run_level == "Limited"
        and snapshot.description == tasks.task_description(tier)
        and arguments
        in {
            item.casefold()
            for item in owned_windows_arguments(kind, tasks, tier, home=home, machine=machine)
        }
        and normalize(snapshot.working_directory) == normalize(tasks.task_working_directory(home))
        and snapshot.trigger_kind == spec.schedule_kind
        and snapshot.trigger_value == spec.schedule_value
    )


def require_windows_retirement(
    kind: str,
    tasks: Any,
    snapshot: Any,
    tier: str,
    *,
    home: Path | None,
    machine: str | None = None,
) -> None:
    pid = live_tier_lock_owner(kind, tier, home)
    if pid is not None:
        raise RetirementBusy(
            f"legacy {kind} update lock is active (pid {pid}); no scheduler mutation"
        )
    if not snapshot.present:
        return
    if not windows_definition_owned(kind, tasks, snapshot, tier, home=home, machine=machine):
        raise RetirementRefused("same-name task does not match an exact owned legacy definition")
    if snapshot.state in {"Running", "Queued"}:
        raise RetirementBusy(f"legacy task is {snapshot.state}; installer must finish")
    if snapshot.state not in {"Ready", "Disabled"} or type(snapshot.enabled) is not bool:
        raise RetirementRefused("legacy task inactivity cannot be verified")


def owned_linux_service_bodies(
    kind: str,
    tasks: Any,
    tier: str,
    *,
    home: Path | None,
    machine: str | None = None,
) -> set[str]:
    current = (
        tasks.render_linux_service_unit(tier, home=home, machine=machine)
        if kind == "self"
        else tasks.render_linux_service_unit(tier, home=home)
    )
    bodies = {current}
    if kind == "fleet":
        return bodies | historical_linux_bodies(tasks, home=home)
    bodies.update(
        "".join(
            line for line in body.splitlines(keepends=True) if not line.startswith("Environment=")
        )
        for body in tuple(bodies)
    )
    return bodies


def inspect_linux_definition(
    kind: str,
    tasks: Any,
    tier: str,
    *,
    home: Path | None,
    machine: str | None = None,
) -> tuple[bool, Path, str]:
    directories = linux_unit_directories(tasks, home)
    unit = tasks.TIER_SPECS[tier].task_name
    if len(directories) != 1:
        raise RetirementRefused(
            "XDG_CONFIG_HOME differs from the legacy scheduler directory; "
            f"inspect both {directories[0]} and {directories[1]} before retirement/adoption"
        )
    directory = directories[0]
    service, timer = directory / f"{unit}.service", directory / f"{unit}.timer"
    present = any(path.exists() or path.is_symlink() for path in (service, timer))
    body = ""
    for path in (service, timer):
        if path.is_symlink():
            raise RetirementRefused(f"legacy unit is a symbolic link: {path}")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        expected = (
            owned_linux_service_bodies(
                kind,
                tasks,
                tier,
                home=home,
                machine=machine or (definition_machine(text) if kind == "self" else None),
            )
            if path == service
            else {tasks.render_linux_timer_unit(tier)}
        )
        if text not in expected:
            raise RetirementRefused(f"same-name unit does not match an exact owned body: {path}")
        if path == service:
            body = text
    return present, directory, body


def require_linux_retirement(
    kind: str,
    tasks: Any,
    tier: str,
    *,
    runner: Callable[..., Any],
    home: Path | None,
    machine: str | None = None,
) -> None:
    pid = live_tier_lock_owner(kind, tier, home)
    if pid is not None:
        raise RetirementBusy(
            f"legacy {kind} update lock is active (pid {pid}); no scheduler mutation"
        )
    _, directory, _ = inspect_linux_definition(kind, tasks, tier, home=home, machine=machine)
    unit = tasks.TIER_SPECS[tier].task_name
    for suffix in ("service", "timer"):
        activity = query_unit_activity(f"{unit}.{suffix}", runner=runner)
        if activity.load_state == "loaded":
            expected = directory / f"{unit}.{suffix}"
            if (
                activity.drop_ins
                or not expected.is_file()
                or activity.fragment_path != str(expected)
            ):
                raise RetirementRefused(f"loaded {suffix} ownership cannot be verified")
        if suffix == "service" and activity.in_flight:
            raise RetirementBusy(
                "legacy service has an active/queued update; no scheduler mutation"
            )
        if suffix == "timer" and (
            activity.job
            or activity.active_state in {"activating", "reloading", "deactivating"}
            or (activity.active_state == "active" and activity.sub_state != "waiting")
        ):
            raise RetirementBusy("legacy timer has a pending dispatch; no scheduler mutation")
