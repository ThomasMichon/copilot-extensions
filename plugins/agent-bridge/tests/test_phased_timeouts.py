"""Tests for phased-timeout config parsing and defaults."""

from __future__ import annotations

import yaml

from agent_bridge.models import PhasedTimeouts, ServiceConfig


class TestPhasedTimeouts:
    def test_defaults(self) -> None:
        cfg = ServiceConfig()
        assert cfg.timeouts.codespace_boot > 0
        assert cfg.timeouts.session_start > 0
        assert cfg.timeouts.command > 0
        # Cold-boot is the most generous, a single turn the longest cap.
        assert cfg.timeouts.codespace_boot >= cfg.timeouts.session_start

    def test_parsed_from_yaml(self, tmp_path, monkeypatch) -> None:
        config_dir = tmp_path
        (config_dir / "config.yaml").write_text(
            yaml.dump({
                "timeouts": {
                    "codespace_boot": 240,
                    "session_start": 30,
                    "command": 600,
                }
            })
        )
        monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(config_dir))

        from agent_bridge.config import load_config

        cfg = load_config()
        assert cfg.timeouts.codespace_boot == 240
        assert cfg.timeouts.session_start == 30
        assert cfg.timeouts.command == 600

    def test_partial_override_keeps_other_defaults(self) -> None:
        t = PhasedTimeouts(command=99)
        assert t.command == 99
        assert t.codespace_boot == PhasedTimeouts().codespace_boot
