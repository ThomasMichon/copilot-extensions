from __future__ import annotations

from pathlib import Path
import shutil
import sys

import pytest

from agent_procutil.owned_process import run_owned
from worktree_manager import manager_config, source_config


def test_restart_recovery_defaults_off_without_creating_config(tmp_path):
    config = manager_config.load_config(tmp_path)

    assert config.restart_recovery.shutdown_notifications is False
    assert config.restart_recovery.automatic_restore is False
    assert not source_config.config_path(tmp_path).exists()


def test_non_utf8_config_raises_normalized_configuration_error(tmp_path):
    source_config.config_path(tmp_path).write_bytes(b"\xff\xfe")
    with pytest.raises(manager_config.ManagerConfigError, match="could not read"):
        manager_config.load_config(tmp_path)


def test_restart_recovery_loads_explicit_local_preferences(tmp_path):
    source_config.config_path(tmp_path).write_text(
        "[restart_recovery]\nshutdown_notifications = true\nautomatic_restore = true\n",
        encoding="utf-8",
    )

    config = manager_config.load_config(tmp_path)

    assert config.restart_recovery.shutdown_notifications is True
    assert config.restart_recovery.automatic_restore is True
    assert config.ahp.endpoint_url == ""


@pytest.mark.parametrize("body", [
    'restart_recovery = "enabled"',
    "restart_recovery = true",
    "restart_recovery = []",
    '[restart_recovery]\nshutdown_notifications = "true"',
    "[restart_recovery]\nshutdown_notifications = 1",
    "[restart_recovery]\nautomatic_restore = 0",
    '[restart_recovery]\nautomatic_restore = "false"',
    "[restart_recovery]\nautomatic_restroe = true",
    "[restart_recovery]\ncommand = 'example'",
])
def test_restart_recovery_rejects_invalid_or_unknown_configuration(tmp_path, body):
    source_config.config_path(tmp_path).write_text(body, encoding="utf-8")

    with pytest.raises(manager_config.ManagerConfigError, match="restart_recovery"):
        manager_config.load_config(tmp_path)


def test_source_updates_preserve_restart_preferences(tmp_path):
    path = source_config.config_path(tmp_path)
    body = "[restart_recovery]\nshutdown_notifications = true\n"
    path.write_text(body, encoding="utf-8")

    source_config.set_source(ref="canary", root=tmp_path)
    source_config.reset_source(root=tmp_path)

    assert path.read_text(encoding="utf-8") == body


def test_ahp_config_defaults_off(tmp_path):
    config = manager_config.load_config(tmp_path)

    assert config.ahp.endpoint_url == ""
    assert config.ahp.account == ""
    assert config.ahp.protocol_versions == ("0.7.0",)


def test_maintenance_defaults_enabled_without_writing_config(tmp_path):
    assert manager_config.load_config(tmp_path).maintenance.automatic is True
    assert not source_config.config_path(tmp_path).exists()


@pytest.mark.parametrize("enabled", [True, False])
def test_maintenance_strict_boolean_and_source_preservation(tmp_path, enabled):
    path = source_config.config_path(tmp_path)
    body = f"[maintenance]\nautomatic = {str(enabled).lower()}\n"
    path.write_text(body, encoding="utf-8")
    assert manager_config.load_config(tmp_path).maintenance.automatic is enabled
    source_config.set_source(ref="canary", root=tmp_path)
    source_config.reset_source(root=tmp_path)
    assert path.read_text(encoding="utf-8") == body


@pytest.mark.parametrize("body", [
    "maintenance = false",
    "maintenance = []",
    "[maintenance]\nautomatic = 0",
    "[maintenance]\nautomatic = 'false'",
    "[maintenance]\nautomatic = []",
    "[maintenance]\nautomtaic = false",
])
def test_maintenance_rejects_invalid_or_unknown_settings(tmp_path, body):
    source_config.config_path(tmp_path).write_text(body, encoding="utf-8")
    with pytest.raises(manager_config.ManagerConfigError, match="maintenance"):
        manager_config.load_config(tmp_path)


def test_maintenance_admission_rereads_and_fails_closed(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    path = source_config.config_path(tmp_path)
    assert manager_config.automatic_maintenance_enabled() is True
    path.write_text("[maintenance]\nautomatic = false", encoding="utf-8")
    assert manager_config.automatic_maintenance_enabled() is False
    path.write_text("[maintenance]\nautomatic = true", encoding="utf-8")
    assert manager_config.automatic_maintenance_enabled() is True
    path.write_text("[maintenance]\nautomatic = 'false'", encoding="utf-8")
    assert manager_config.automatic_maintenance_enabled() is False
    assert "Automatic maintenance blocked" in caplog.text


def test_maintenance_unreadable_config_blocks(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    source_config.config_path(tmp_path).mkdir()
    assert manager_config.automatic_maintenance_enabled() is False
    assert "could not read" in caplog.text


@pytest.mark.parametrize(("body", "expected", "diagnostic"), [
    (None, 0, ""),
    ("[maintenance]\nautomatic = true\n", 0, ""),
    ("[maintenance]\nautomatic = false\n", 1, "Automatic maintenance disabled"),
    ("[maintenance]\nautomatic = 'false'\n", 2, "Automatic maintenance blocked"),
])
def test_launch_maintenance_probe_preserves_payload_bytes(
    tmp_path, monkeypatch, body, expected, diagnostic,
):
    source = Path(__file__).resolve().parents[1]
    payload = tmp_path / "payload"
    package = payload / "src" / "worktree_manager"
    package.mkdir(parents=True)
    (payload / "bin").mkdir()
    for name in ("__init__.py", "manager_config.py", "source_config.py"):
        shutil.copyfile(source / "src" / "worktree_manager" / name, package / name)
    probe = payload / "bin" / "check-automatic-maintenance.py"
    shutil.copyfile(source / "bin" / probe.name, probe)
    root = tmp_path / "manager"
    root.mkdir()
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    if body is not None:
        (root / "config.toml").write_text(body, encoding="utf-8")
    before = {p.relative_to(payload): p.read_bytes()
              for p in payload.rglob("*") if p.is_file()}
    for _ in range(2):
        # Ignore the test runner's bytecode environment; the real probe owns it.
        result = run_owned([sys.executable, "-E", str(probe)], timeout=30)
        assert result.returncode == expected, result.stdout + result.stderr
        assert result.stdout == ""
        assert diagnostic in result.stderr
        assert not list(payload.rglob("__pycache__"))
        assert {p.relative_to(payload): p.read_bytes()
                for p in payload.rglob("*") if p.is_file()} == before


def test_ahp_config_loads_general_provider_settings(tmp_path):
    source_config.config_path(tmp_path).write_text(
        """
[ahp]
endpoint_url = "ws://127.0.0.1:8765"
account = "example-user"
protocol_versions = ["0.7.0", "0.8.0"]
auth_resource = "https://api.example.com"
connect_timeout_seconds = 4
lifecycle_timeout_seconds = 25.5
""".strip(),
        encoding="utf-8",
    )

    config = manager_config.load_config(tmp_path)

    assert config.ahp.endpoint_url == "ws://127.0.0.1:8765"
    assert config.ahp.account == "example-user"
    assert config.ahp.protocol_versions == ("0.7.0", "0.8.0")
    assert config.ahp.connect_timeout_seconds == 4.0
    assert config.ahp.lifecycle_timeout_seconds == 25.5


def test_source_updates_preserve_ahp_config(tmp_path):
    path = source_config.config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '[ahp]\nendpoint_url = "ws://127.0.0.1:8765"\n',
        encoding="utf-8",
    )

    source_config.set_source(ref="canary", root=tmp_path)
    source_config.reset_source(root=tmp_path)

    assert path.read_text(encoding="utf-8") == (
        '[ahp]\nendpoint_url = "ws://127.0.0.1:8765"\n'
    )
