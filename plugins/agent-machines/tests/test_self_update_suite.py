"""The suite phase shares Manager's real OS transaction lock."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from agent_procutil import no_window_kwargs
from zdd.cutover_lock import CutoverLock

from agent_machines import automatic_update_ownership as ownership
from agent_machines import cli_fleet_update, fleet_update, self_update, self_update_suite


@pytest.fixture(autouse=True)
def policy_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))


def test_self_update_suite_holds_canonical_lock_across_process_boundary(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **kwargs: Any) -> self_update.CommandResult:
        calls.append(argv)
        code = (
            "import sys\n"
            "from zdd.cutover_lock import CutoverLock, CutoverLockedError\n"
            "try:\n"
            "    with CutoverLock(sys.argv[1]):\n"
            "        sys.exit(0)\n"
            "except CutoverLockedError:\n"
            "    sys.exit(75)\n"
        )
        result = subprocess.run(  # noqa: S603 - fixed probe in the isolated test interpreter
            [
                sys.executable,
                "-c",
                code,
                str(ownership.suite_update_transaction(tmp_path).path.parent),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            **no_window_kwargs(),
        )
        assert result.returncode == 75, result.stderr
        return self_update.CommandResult(argv, 0, "ok")

    result = self_update_suite.run_suite_updates(["example"], runner=runner, home=tmp_path)
    assert result.status == "ok"
    assert calls == [["agent-worktrees", "-p", "example", "update", "--no-manager"]]
    with ownership.suite_update_transaction(tmp_path):
        pass  # The transaction is released before dependency restore.


def test_self_update_suite_rechecks_ownership_after_enrollment_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ownership.read_update_ownership(tmp_path).state == "standalone"
    real_transaction = ownership.suite_update_transaction

    def raced_transaction(home: Path | None) -> CutoverLock:
        path = ownership.ownership_policy_path(home)
        path.parent.mkdir(parents=True)
        path.write_text(
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
        return real_transaction(home)

    monkeypatch.setattr(self_update_suite, "suite_update_transaction", raced_transaction)
    result = self_update_suite.run_suite_updates(
        ["example"],
        home=tmp_path,
        runner=lambda *args, **kwargs: pytest.fail("enrollment won; no raw suite updater"),
    )
    assert result.status == "ok"
    assert result.steps[0].status == "skipped"
    assert "manager" in result.detail


def test_self_update_suite_contended_transaction_preserves_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(self_update, "sweep_repo_paths", lambda *args, **kwargs: [])

    def runner(argv: list[str], **kwargs: Any) -> self_update.CommandResult:
        calls.append(argv)
        return self_update.CommandResult(argv, 0, "ok")

    with ownership.suite_update_transaction(tmp_path):
        result = self_update.run_tier("sweep", opted_in=True, runner=runner, home=tmp_path)
    assert result.status == "deferred"
    assert len(calls) == 1
    assert calls[0][:2] == ["agent-machines", "restore"]


def test_self_update_suite_explicit_disabled_default_provenance_is_not_absent(
    tmp_path: Path,
) -> None:
    path = ownership.ownership_policy_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.automatic-updates",
                "version": 1,
                "owner": "disabled",
                "provenance": "default",
                "user_disabled": False,
                "interval_seconds": 3600,
                "project": None,
                "external_controller": None,
            }
        ),
        encoding="utf-8",
    )
    result = self_update_suite.run_suite_updates(
        ["example"],
        home=tmp_path,
        runner=lambda *args, **kwargs: pytest.fail(
            "explicit disabled policy forbids legacy update"
        ),
    )
    assert result.steps[0].status == "skipped"


def test_self_update_suite_inventory_cli_has_no_requirement_discovery(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_fleet_update,
        "_resolve_fleet_update_resources",
        lambda *args: pytest.fail("inventory must not resolve requirement packages"),
    )
    monkeypatch.setattr(cli_fleet_update, "inventory_legacy_updates", lambda: [])
    assert cli_fleet_update.cmd_inventory(argparse.Namespace(json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "copilot-extensions.legacy-update-inventory"
    assert payload["version"] == 1
    assert payload["adoption_ready"]


def test_self_update_suite_enrollment_transaction_also_blocks_fleet_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fleet_update,
        "_reconcile_scheduled_task",
        lambda *args, **kwargs: pytest.fail("enrollment lock must prevent registration"),
    )
    with ownership.suite_update_transaction(tmp_path):
        result = fleet_update.reconcile_scheduled_task(
            "sweep",
            desired_present=True,
            home=tmp_path,
        )
    assert result.status == "deferred"
    assert not result.changed
