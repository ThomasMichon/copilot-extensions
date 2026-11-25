"""An in-process update participant must not queue work behind its own request."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

import update_driver
from agent_worktrees import config, update_cli, update_driver_cli


def test_in_process_participant_runs_phase_under_its_own_source_recovery_custody(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    tmp_path = tmp_path.resolve()
    script = tmp_path / "tools" / "update.py"
    script.parent.mkdir()
    script.write_text("pass\n", encoding="utf-8")
    repo = config.RepoConfig(
        anchor=str(tmp_path), worktree_root=str(tmp_path / "worktrees"),
        update_controller={"windows": "tools/update.py"},
    )
    monkeypatch.setattr(config, "active_project", lambda: "example")
    monkeypatch.setattr(
        config, "load_config", lambda **_kwargs: SimpleNamespace(default_repo=repo),
    )
    monkeypatch.setattr(config, "detect_platform", lambda: "windows")
    home = tmp_path / "home"
    home.mkdir()
    driver_root = home / ".copilot-extensions" / "update-driver"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("UPDATE_DRIVER_WITNESS", raising=False)
    phases: list[str] = []

    def run_phase(selected: argparse.Namespace) -> int:
        phases.append(selected.phase)
        return 0

    def helper(name: str, local: Callable[..., Any]) -> Callable[..., Any]:
        return run_phase if name == "_cmd_update_in_plugin" else local

    monkeypatch.setattr(update_cli, "_core_helper", helper)
    request = update_driver.enqueue(
        update_driver.RequestSpec("source-recovery", ("install",), cwd=str(tmp_path)),
        runner=update_driver.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
        root=driver_root,
    )

    def execute(transaction: Any) -> int:
        # Source recovery's execution plan scopes custody, which consumes the
        # ambient witness, and then drives the engine CLI in-process.
        custody = update_driver.borrowed_custody(
            root=driver_root, environ=transaction.participant_env(),
        )
        assert custody is not None
        with custody:
            return update_driver_cli.cmd_update_request(
                argparse.Namespace(phase="payloads", no_manager=True),
            )

    assert update_driver.drain(root=driver_root, executor=execute) == 0
    assert update_driver.status(request.id, root=driver_root).exit_code == 0
    assert phases == ["payloads"]
    assert [row.id for row in update_driver.status(root=driver_root)] == [request.id]
