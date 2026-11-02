"""Synchronous UI actions use worker publication after configuration expiry."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager.production_picker import project_config as cfg
from worktree_manager.production_picker.picker_tui import data_local, data_ssh, maintenance
from worktree_manager.production_picker.picker_tui.engine_maintenance_actions import (
    PickerScreenMaintenanceActionsMixin,
)
from worktree_manager.production_picker.picker_tui.engine_worktree_actions import (
    PickerScreenWorktreeActionsMixin,
)

from test_local_source_admission import LABELS, _machines, _setup

pytestmark = [
    pytest.mark.portfolio_tier("T1"),
    pytest.mark.effect("filesystem"),
    pytest.mark.contract("worktree_manager.local_source_admission"),
]


@pytest.mark.parametrize("platform", LABELS)
@pytest.mark.parametrize("binding", [None, "preferred-host"])
@pytest.mark.parametrize("stale", ["expired", "invalidated"])
def test_local_ui_actions_retain_published_identity_without_config_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str,
    binding: str | None, stale: str,
) -> None:
    now = [0.0]
    monkeypatch.setattr(cfg, "_file_clock", lambda: now[0])
    _setup(monkeypatch, tmp_path, platform, _machines("unrelated", platform), configured=binding)
    published = next(source for source in data_ssh._build_sources() if source.local).key
    row = {"raw": {"id": "original-record"}, "machine": published[0], "env": published[1]}
    dialogs, executions, requests = [], [], []

    class Executor:
        def __init__(self, op: str, tasks: list) -> None:
            executions.append((op, tasks))

        def start(self) -> None:
            pass

    monkeypatch.setattr(maintenance, "MaintenanceExecutor", Executor)
    monkeypatch.setattr(
        maintenance.engine_client, "run_json",
        lambda project, args, **kwargs: requests.append((project, args)) or {"ok": True},
    )
    monkeypatch.setattr(
        data_ssh, "remote_op_argv", lambda *args, **kwargs: pytest.fail("local UI action used SSH"),
    )
    screen = SimpleNamespace(
        src=data_local, _src_local=lambda: published, create_target=lambda: published,
        real_ops=False, _local_model_hosts=lambda: set(), _decide=lambda result: None,
        app=SimpleNamespace(push_screen=lambda dialog, callback: dialogs.append(dialog)),
        _current_tab=lambda: {"machine": published[0], "env": published[1]},
    )
    if stale == "expired":
        now[0] = 60.0
    else:
        cfg.clear_caches()

    async def exercise() -> None:
        with pytest.raises(RuntimeError, match="configuration is not ready"):
            cfg.load_config()
        PickerScreenMaintenanceActionsMixin._open_optmenu(screen)
        assert dialogs[0]._launch_local
        assert any(option["label"] == "AHP" for option in dialogs[0]._dlg["opts"])
        assert PickerScreenWorktreeActionsMixin._embody_task_cli(screen, {}) == (False, "no task id")
        screen.real_ops = True
        for operation in ("sync", "cleanup", "restart"):
            screen.progress = {
                "op": operation, "recs": [row],
                "include_unused": False, "include_conversations": False,
            }
            PickerScreenMaintenanceActionsMixin._start_progress(screen)
            assert executions[-1][0] == operation
            assert executions[-1][1][0][1]() == {"ok": True}
        assert len(requests) == 3

    try:
        asyncio.run(exercise())
    finally:
        cfg.clear_caches(reset_binding=True)
