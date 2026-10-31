"""Local-only entry and legacy target spellings share the selected source key."""
from __future__ import annotations

from pathlib import Path

import pytest

from worktree_manager.production_picker import project_config as cfg, runner
from worktree_manager.production_picker.picker_tui import data_local, data_ssh, roster

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
@pytest.mark.parametrize("kind", ["absent", "empty", "unrelated", "matching", "unscoped", "sibling"])
@pytest.mark.parametrize("binding", [None, "preferred-host"])
def test_actual_local_only_entry_uses_the_live_source_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str,
    kind: str, binding: str | None,
) -> None:
    root = _setup(monkeypatch, tmp_path, platform, _machines(kind, platform), configured=binding)
    raw = {"id": "original-record", "machine": "retained-owner", "title": "Retained"}
    monkeypatch.setattr(
        data_local.engine_client, "list_worktree_rows", lambda *args, **kwargs: [raw],
    )
    expected = next(source for source in data_ssh._build_sources() if source.local)

    def render(*, source: object, live: bool, **kwargs: object) -> dict:
        assert source is data_local and not live
        assert [(machine, env) for _, machine, env, _ in data_local.machines()] == [expected.key]
        row = data_local.load()[0]
        refreshed = data_local.refresh_one(raw["id"])
        for selected in (row, refreshed):
            assert (selected["machine"], selected["env"]) == expected.key
            assert selected["source_id"] == expected.source_id
            assert selected["id"] == raw["id"]
            assert data_local.is_local(selected["machine"], selected["env"])
        return row

    monkeypatch.setattr(runner, "run_tui_picker", render)
    result = runner.run("example", local=True, mock_mode=False, bootstrap_payload={
        "version": 1, "project": "example", "should_switch_cwd": False, "default_live": True,
        "project_config": {
            "config_dir": str(tmp_path / "project"), "repo_dir": str(root),
            "machine": binding or ("raw-host-wsl" if platform == "wsl" else "raw-host"),
            "configured_machine": binding,
        },
    })
    assert result["id"] == "original-record"
    assert raw["machine"] == "retained-owner"


@pytest.mark.parametrize("kind", ["absent", "empty", "unrelated"])
@pytest.mark.parametrize("target", ["raw-host (WSL)", "RAW-HOST (WSL)", "raw-host-wsl"])
def test_validated_legacy_wsl_target_names_the_local_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, target: str,
) -> None:
    _setup(monkeypatch, tmp_path, "wsl", _machines(kind, "wsl"))
    assert data_local.is_local(target, "WSL")
    assert not data_local.is_local("raw-host (Windows)", "WSL")
    assert not data_local.is_local("foreign-host (WSL)", "WSL")


@pytest.mark.parametrize("platform,label", [("windows", "Windows"), ("windows", "Win")])
def test_legacy_customization_key_is_preserved_by_source_and_profiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, label: str,
) -> None:
    key = f"friendly ({label})"
    _setup(monkeypatch, tmp_path, platform, {
        key: {
            "hostname": "raw-host", "ssh": {
                "ready": True, "environments": [{"name": platform, "alias": "local-profile"}],
            },
        },
    })
    local = next(source for source in data_ssh._build_sources() if source.local)
    assert local.machine == key
    assert roster.local_host() == local.key
    assert any(column[1:] == local.key for column in roster.host_cols())
    assert local.key in roster.target_envs()
    assert data_local.is_local(*local.key)
