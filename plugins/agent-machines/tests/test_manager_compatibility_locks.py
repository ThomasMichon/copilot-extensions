"""Compatibility fallback keeps custody; pre-flag jobs remain retirement blockers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from agent_procutil import no_window_kwargs

from agent_machines import (
    fleet_update,
    fleet_update_tasks,
    legacy_update_inventory,
    manager_compatibility,
    self_update,
)
from agent_machines.legacy_fleet_definitions import historical_linux_bodies

from ._manager_compatibility_helpers import ManagerProtocol, default_policy, installed_manager
from ._scheduler_safety_helpers import unit_properties


def competing_lock_exit(root: Path) -> int:
    code = (
        "import sys\n"
        "from zdd.cutover_lock import CutoverLock, CutoverLockedError\n"
        "try:\n"
        "    with CutoverLock(sys.argv[1]):\n"
        "        sys.exit(0)\n"
        "except CutoverLockedError:\n"
        "    sys.exit(75)\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed isolated interpreter/lock probe
        [sys.executable, "-c", code, str(root / "automatic-updates/update-transaction")],
        capture_output=True,
        text=True,
        timeout=10,
        **no_window_kwargs(),
    )
    assert result.returncode in {0, 75}, result.stderr
    return result.returncode


@pytest.mark.parametrize("guarded", [False, True])
def test_manager_compatibility_real_crossprocess_custody(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guarded: bool,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=guarded)

    def update(argv: list[str]) -> fleet_update.CommandResult:
        assert competing_lock_exit(installation.root) == (0 if guarded else 75)
        return fleet_update.CommandResult(argv, 0, "simulated update")

    runner = ManagerProtocol(installation, guarded, update=update)
    result = manager_compatibility.run_compatible_manager_update(
        runner=runner,
        home=installation.home,
    )
    assert result.status == "ok", result.detail
    assert len(runner.updates) == 1
    assert competing_lock_exit(installation.root) == 0


def test_manager_compatibility_new_guard_after_legacy_probe_defers_without_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch)
    installations: list[list[str]] = []

    def replacement_guard(argv: list[str]) -> fleet_update.CommandResult:
        assert "--automatic-trigger" not in argv
        (installation.slot / "src/worktree_manager/__main__.py").write_text(
            "# replacement now owns canonical update admission\n",
            encoding="utf-8",
        )
        locked = competing_lock_exit(installation.root) == 75
        if not locked:
            installations.append(argv)
        return fleet_update.CommandResult(argv, 5 if locked else 0, "transaction held")

    runner = ManagerProtocol(installation, False, update=replacement_guard)
    result = manager_compatibility.run_compatible_manager_update(
        runner=runner,
        home=installation.home,
    )
    assert result.status == "deferred" and result.returncode == 5
    assert installations == []
    assert len(runner.updates) == 1


def test_manager_compatibility_corrupt_policy_never_queries_or_updates_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch)
    (installation.root / "automatic-updates.json").write_text("malformed", encoding="utf-8")
    runner = ManagerProtocol(installation, False)
    result = manager_compatibility.run_compatible_manager_update(
        runner=runner,
        home=installation.home,
    )
    assert result.status == "error"
    assert runner.calls == []


@pytest.mark.parametrize("wrapper", [False, True])
def test_manager_compatibility_disabled_policy_does_not_hide_remaining_trigger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wrapper: bool,
) -> None:
    if os.name == "nt":
        pytest.skip("Linux unit-file inventory fixture")
    installation = installed_manager(tmp_path, monkeypatch)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    directory = fleet_update_tasks._linux_unit_dir(installation.home)
    directory.mkdir(parents=True)
    body = (
        fleet_update_tasks.render_linux_service_unit("sweep", home=installation.home)
        if wrapper
        else next(
            body
            for body in historical_linux_bodies(fleet_update_tasks, home=installation.home)
            if "Environment=" in body and "--automatic-trigger" not in body
        )
    )
    name = fleet_update_tasks.TIER_SPECS["sweep"].task_name
    (directory / f"{name}.service").write_text(body, encoding="utf-8")
    (directory / f"{name}.timer").write_text(
        fleet_update_tasks.render_linux_timer_unit("sweep"),
        encoding="utf-8",
    )
    (installation.root / "automatic-updates.json").write_text(
        json.dumps(default_policy()),
        encoding="utf-8",
    )
    before = {path.name: path.read_bytes() for path in directory.iterdir()}

    def systemd(argv: list[str], **kwargs: Any) -> fleet_update.CommandResult:
        verb = argv[2]
        if verb == "is-system-running":
            value = "running"
        elif verb == "is-enabled":
            value = "enabled"
        elif verb == "show":
            value = unit_properties(
                argv[3],
                directory,
                load="loaded" if (directory / argv[3]).exists() else "not-found",
            )
        else:
            pytest.fail(f"inventory attempted an unsupported or mutating command: {argv}")
        return fleet_update.CommandResult(argv, 0, value)

    monkeypatch.setattr(fleet_update, "shutil_which", lambda name: name)
    monkeypatch.setattr(self_update, "shutil_which", lambda name: name)
    artifacts = legacy_update_inventory.inventory_legacy_updates(
        home=installation.home,
        runner=systemd,
    )
    fleet = artifacts[0]
    assert fleet.inspection_complete
    assert fleet.retirement_required and fleet.blocks_adoption
    assert fleet.preflag_trigger is (not wrapper)
    assert fleet.trigger_kind == ("am-wrapper" if wrapper else "direct-manager-preflag")
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}
    runner = ManagerProtocol(installation, False)
    result = fleet_update.run_tier(
        "sweep",
        opted_in=True,
        home=installation.home,
        runner=runner,
    )
    assert result.status == "deferred"
    assert runner.calls == []
