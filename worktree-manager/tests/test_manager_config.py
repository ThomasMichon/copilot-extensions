from __future__ import annotations

import pytest

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
