"""Setup, New and maintenance consume the selected local source contract."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager.production_picker import project_config as cfg
from worktree_manager.production_picker.picker_tui import data_local, data_ssh, maintenance, roster
from worktree_manager.production_picker.picker_tui.engine_loading import PickerScreenLoadingMixin
from worktree_manager.production_picker.picker_tui.engine_maintenance_actions import (
    PickerScreenMaintenanceActionsMixin,
)

from test_local_source_admission import LABELS, _machines, _setup

pytestmark = [
    pytest.mark.portfolio_tier("T1"),
    pytest.mark.effect("filesystem"),
    pytest.mark.contract("worktree_manager.local_source_admission"),
]


@pytest.fixture(autouse=True)
def _reset_config():
    yield
    cfg.clear_caches(reset_binding=True)


@pytest.mark.parametrize("platform", LABELS)
@pytest.mark.parametrize("binding", [None, "preferred-host"])
def test_setup_new_dialog_and_maintenance_use_selected_local_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, binding: str | None,
) -> None:
    _setup(monkeypatch, tmp_path, platform, _machines("unrelated", platform), configured=binding)
    expected = next(source for source in data_ssh._build_sources() if source.local)
    prepared = PickerScreenLoadingMixin._prepare_live_source(SimpleNamespace(src=data_local), None)
    assert prepared["local"] == expected.key
    dialogs = []
    screen = SimpleNamespace(
        src=data_local, _src_local=lambda: expected.key,
        create_target=lambda: expected.key, real_ops=False,
        _local_model_hosts=lambda: set(), _decide=lambda result: None,
        app=SimpleNamespace(push_screen=lambda dialog, callback: dialogs.append(dialog)),
    )
    PickerScreenMaintenanceActionsMixin._open_optmenu(screen)
    assert dialogs[0]._launch_local
    assert any(option["label"] == "AHP" for option in dialogs[0]._dlg["opts"])
    calls = []
    monkeypatch.setattr(
        maintenance.engine_client, "run_json",
        lambda project, argv, **kwargs: calls.append((project, argv)) or {"ok": True},
    )
    monkeypatch.setattr(
        data_ssh, "remote_op_argv", lambda *args, **kwargs: pytest.fail("local maintenance used SSH"),
    )
    row = {
        "id": "original-record", "raw": {"id": "original-record"},
        "machine": expected.machine, "env": expected.env, "source_id": expected.source_id,
    }
    for operation in ("sync", "cleanup", "restart"):
        task = maintenance.build_tasks(operation, [row], data_local)[0][1]
        assert task() == {"ok": True}
    assert len(calls) == 3 and all(project == "example" for project, _ in calls)


@pytest.mark.parametrize("platform", LABELS)
@pytest.mark.parametrize("shape", ["full", "short", "absent"])
def test_full_observation_and_legacy_short_customization_remain_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, shape: str,
) -> None:
    machines = None if shape == "absent" else {
        "local-space": {
            "hostname": "raw-host.example.test" if shape == "full" else "raw-host",
            "execution_platform": platform,
        },
    }
    _setup(monkeypatch, tmp_path, platform, machines)
    monkeypatch.setattr("socket.gethostname", lambda: "raw-host.example.test")
    local = next(source for source in data_ssh._build_sources() if source.local)
    expected = (
        "local-space" if shape != "absent" else
        "raw-host.example.test-wsl" if platform == "wsl" else "raw-host.example.test"
    )
    assert local.key == (expected, LABELS[platform])
    assert local.ready and local.argv is None
    assert roster.local_host() == local.key
    assert data_local.is_local(*local.key)
    assert data_local.machines()[0][1:3] == local.key
