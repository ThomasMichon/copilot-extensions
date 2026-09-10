from __future__ import annotations

from worktree_manager import manager_config, source_config


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
