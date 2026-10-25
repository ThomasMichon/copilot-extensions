"""Read-only evidence about legacy suite-update triggers and active runs."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .legacy_fleet_definitions import classify_fleet_trigger
from .scheduler_retirement import (
    RetirementRefused,
    inspect_linux_definition,
    linux_unit_directories,
    live_tier_lock_owner,
    query_unit_activity,
    windows_definition_owned,
)


@dataclass(frozen=True)
class LegacyUpdateArtifact:
    kind: str
    identity: str
    present: bool | None
    enabled: bool | None
    in_flight: bool | None
    preflag_trigger: bool | None
    retirement_required: bool
    detail: str
    lock_owner_pid: int | None = None
    execute: str | None = None
    arguments: str | None = None
    inspection_complete: bool = True
    trigger_kind: str | None = None

    @property
    def blocks_adoption(self) -> bool:
        return (
            not self.inspection_complete or self.retirement_required or self.in_flight is not False
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "blocks_adoption": self.blocks_adoption}


def _live_lock_owner(kind: str, home: Path | None) -> int | None:
    return live_tier_lock_owner(kind, "sweep", home)


def inventory_legacy_updates(
    *,
    home: Path | None = None,
    runner: Callable[..., Any] | None = None,
) -> list[LegacyUpdateArtifact]:
    """Never stop, unregister, enable, or rewrite an artifact.

    A self sweep remains useful maintenance, but any active old invocation
    must finish before another updater can safely assume ownership.
    """
    from . import fleet_update, fleet_update_tasks, self_update, self_update_tasks

    artifacts: list[LegacyUpdateArtifact] = []
    for kind, engine, tasks in (
        ("fleet", fleet_update, fleet_update_tasks),
        ("self", self_update, self_update_tasks),
    ):
        resolved_runner = runner or engine.default_command_runner
        identity = tasks.TIER_SPECS["sweep"].task_name
        try:
            lock_pid = _live_lock_owner(kind, home)
            if sys.platform == "linux":
                identity += ".timer"
                directories = linux_unit_directories(tasks, home)
                present = any(
                    (directory / f"{tasks.TIER_SPECS['sweep'].task_name}.{suffix}").exists()
                    for directory in directories
                    for suffix in ("timer", "service")
                )
                if not tasks.linux_systemd_user_available(
                    resolve_binary=engine.shutil_which, runner=resolved_runner
                ):
                    artifacts.append(
                        LegacyUpdateArtifact(
                            kind,
                            identity,
                            present,
                            None,
                            True if lock_pid is not None else None,
                            None,
                            kind == "fleet" and present,
                            "user systemd manager unavailable; complete in-flight state "
                            "cannot be verified"
                            + (
                                "; XDG_CONFIG_HOME differs from legacy scheduler directory"
                                if len(directories) > 1
                                else ""
                            ),
                            lock_pid,
                            inspection_complete=False,
                        )
                    )
                    continue
                present, units, arguments = inspect_linux_definition(
                    kind,
                    tasks,
                    "sweep",
                    home=home,
                )
                name = tasks.TIER_SPECS["sweep"].task_name
                activity = {}
                for suffix in ("service", "timer"):
                    unit = f"{name}.{suffix}"
                    current = query_unit_activity(unit, runner=resolved_runner)
                    if current.load_state == "loaded" and (
                        current.drop_ins
                        or not (units / unit).is_file()
                        or current.fragment_path != str(units / unit)
                    ):
                        raise RetirementRefused(f"loaded {unit} ownership cannot be verified")
                    activity[suffix] = current
                timer = activity["timer"]
                if timer.load_state == "not-found":
                    enabled = False
                else:
                    probe = resolved_runner(
                        ["systemctl", "--user", "is-enabled", name + ".timer"],
                        timeout=30,
                    )
                    value = probe.stdout.strip()
                    if value not in {
                        "enabled",
                        "enabled-runtime",
                        "disabled",
                        "masked",
                        "static",
                        "indirect",
                    }:
                        raise RetirementRefused("legacy timer enablement cannot be verified")
                    enabled = value in {"enabled", "enabled-runtime"}
                    if probe.returncode not in {0, 1} or (enabled and probe.returncode != 0):
                        raise RetirementRefused("legacy timer enablement query failed")
                in_flight = (
                    activity["service"].in_flight
                    or bool(timer.job)
                    or (
                        timer.active_state in {"activating", "reloading", "deactivating"}
                        or (timer.active_state == "active" and timer.sub_state != "waiting")
                    )
                )
                execute = tasks.task_binstub_path(home)
            elif sys.platform == "win32":
                snapshot = tasks.query_scheduled_task(
                    "sweep",
                    runner=resolved_runner,
                    resolve_binary=engine.shutil_which,
                    home=home,
                )
                present = snapshot.present
                if present and type(snapshot.enabled) is not bool:
                    raise RetirementRefused("legacy task enablement cannot be verified")
                if present and not windows_definition_owned(
                    kind,
                    tasks,
                    snapshot,
                    "sweep",
                    home=home,
                ):
                    raise RetirementRefused("same-name task ownership cannot be verified")
                if present and (snapshot.state or "").casefold() not in {
                    "running",
                    "ready",
                    "disabled",
                    "queued",
                }:
                    raise RuntimeError("legacy task activity could not be verified")
                in_flight = (
                    False
                    if not present
                    else None
                    if snapshot.state is None
                    else snapshot.state.casefold() in {"running", "queued"}
                )
                arguments = snapshot.arguments or ""
                enabled, execute = snapshot.enabled, snapshot.execute
            else:
                raise RuntimeError("legacy scheduler inspection is unsupported on this platform")
            trigger = (
                classify_fleet_trigger(
                    tasks,
                    arguments,
                    home=home,
                    windows=sys.platform == "win32",
                )
                if kind == "fleet" and present
                else None
            )
            preflag = trigger == "direct-manager-preflag" if trigger is not None else None
            in_flight = in_flight or lock_pid is not None
            retirement = kind == "fleet" and present
            detail = (
                "legacy fleet trigger requires explicit retirement; no artifacts were changed"
                if retirement
                else "no legacy fleet trigger retirement required"
            )
            if in_flight:
                detail += "; active update must finish before ownership handover"
            artifacts.append(
                LegacyUpdateArtifact(
                    kind,
                    identity,
                    present,
                    enabled,
                    in_flight,
                    preflag,
                    retirement,
                    detail,
                    lock_pid,
                    execute,
                    arguments,
                    trigger_kind=trigger,
                )
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            artifacts.append(
                LegacyUpdateArtifact(
                    kind,
                    identity,
                    None,
                    None,
                    None,
                    None,
                    True,
                    f"legacy update inventory refused: {exc}",
                    inspection_complete=False,
                )
            )
    return artifacts
