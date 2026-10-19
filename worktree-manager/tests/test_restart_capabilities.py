"""Read-only capability inspection never enrolls or contacts host services."""

from __future__ import annotations

import json

import pytest

from worktree_manager import manager_config, restart_observer, self_install, source_config
from worktree_manager.__main__ import main


@pytest.fixture
def inspection_root(tmp_path, monkeypatch):
    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(restart_observer, "native_epoch", lambda: restart_observer.HostEpoch(
        "linux", "example-environment", "example-boot",
    ))
    return tmp_path


def test_capability_inspection_defaults_disabled(inspection_root, capsys):
    assert main(["mux-daemon", "recovery-evidence"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["capabilities"]["shutdown_notifications"]["status"] == "disabled"
    assert result["capabilities"]["automatic_restore"]["status"] == "disabled"
    assert result["automatic_eligible"] is False
    assert list(inspection_root.iterdir()) == []


def test_requested_automatic_mode_is_not_success_shaped(inspection_root, capsys):
    source_config.config_path(inspection_root).write_text(
        "[restart_recovery]\nautomatic_restore = true\n", encoding="utf-8",
    )

    assert main(["mux-daemon", "recovery-evidence"]) == 1
    result = json.loads(capsys.readouterr().out)

    automatic = result["capabilities"]["automatic_restore"]
    assert automatic["requested"] is True
    assert automatic["status"] == "blocked"
    assert "acknowledged" in automatic["blocker"]
    assert result["blocker"]
    assert result["automatic_eligible"] is False
    assert not (inspection_root / "restart-recovery").exists()


@pytest.mark.parametrize("platform", ["linux", "wsl", "windows"])
def test_notification_inspection_never_claims_running_adapter(
    inspection_root, monkeypatch, capsys, platform,
):
    monkeypatch.setattr(restart_observer, "native_epoch", lambda: restart_observer.HostEpoch(
        platform, "example-environment", "example-boot",
    ))
    source_config.config_path(inspection_root).write_text(
        "[restart_recovery]\nshutdown_notifications = true\n", encoding="utf-8",
    )

    assert main(["mux-daemon", "recovery-evidence"]) == 1
    report = json.loads(capsys.readouterr().out)
    notification = report["capabilities"]["shutdown_notifications"]
    assert notification["requested"] is True
    assert notification["status"] == ("unconfirmed" if platform == "linux" else "blocked")
    assert notification["blocker"]
    assert not (inspection_root / "restart-recovery").exists()


def test_invalid_config_is_reported_as_json_blocker(inspection_root, capsys):
    source_config.config_path(inspection_root).write_text(
        "[restart_recovery]\nautomatic_restroe = true\n", encoding="utf-8",
    )

    assert main(["mux-daemon", "recovery-evidence"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "restart_recovery" in report["blocker"]
    assert report["automatic_eligible"] is False
    assert not (inspection_root / "restart-recovery").exists()


def test_manager_config_can_remain_independent_of_runtime():
    assert manager_config.ManagerConfig().restart_recovery.automatic_restore is False


def test_non_utf8_config_is_reported_as_json_blocker(inspection_root, capsys):
    source_config.config_path(inspection_root).write_bytes(
        "[restart_recovery]\n".encode("utf-16"),
    )

    assert main(["mux-daemon", "recovery-evidence"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "could not read" in report["blocker"]
    assert report["automatic_eligible"] is False
    assert not (inspection_root / "restart-recovery").exists()
