"""Tests for phased-timeout config parsing and defaults."""

from __future__ import annotations

from unittest.mock import MagicMock

import yaml

from agent_bridge import __main__ as cli
from agent_bridge.client import BridgeClient
from agent_bridge.models import PhasedTimeouts, ServiceConfig


class TestPhasedTimeouts:
    def test_defaults(self) -> None:
        cfg = ServiceConfig()
        assert cfg.timeouts.codespace_boot == 300.0
        assert cfg.timeouts.session_start == 240.0
        assert cfg.timeouts.session_new == 1200.0
        assert cfg.timeouts.codespace_boot > 0
        assert cfg.timeouts.session_start > 0
        assert cfg.timeouts.session_new > 0
        assert cfg.timeouts.command > 0
        # Cold-boot must cover the handshake budget; a turn has the longest cap.
        assert cfg.timeouts.codespace_boot >= cfg.timeouts.session_start
        # A cold session/new (workspace + skills load) gets a larger budget
        # than the fast initialize handshake (#2107).
        assert cfg.timeouts.session_new >= cfg.timeouts.session_start

    def test_startup_http_budget_covers_all_phases(self, monkeypatch) -> None:
        timeouts = PhasedTimeouts(
            codespace_boot=300,
            ssh_connect=120,
            session_start=240,
            session_new=1200,
        )
        monkeypatch.setattr(cli, "_phased_timeouts", lambda: timeouts)
        assert cli._startup_request_timeout() == 1770.0
        assert cli._startup_request_timeout(resume=True) == 5310.0
        assert cli._startup_request_timeout(
            resume=True,
            fresh_fallback=True,
        ) == 7080.0

        timeouts.session_host_ready = 600
        assert cli._startup_request_timeout() == 2070.0

    def test_client_forwards_startup_request_timeout(self) -> None:
        client = object.__new__(BridgeClient)
        client._request = MagicMock(return_value={})

        client.start_session(agent="container:repo-1", request_timeout=1770.0)
        assert client._request.call_args.kwargs["request_timeout"] == 1770.0

        client.resume_session("session-1", request_timeout=1770.0)
        assert client._request.call_args.kwargs["request_timeout"] == 1770.0

        client.resume_worktree("worktree-1", request_timeout=5310.0)
        assert client._request.call_args.kwargs["request_timeout"] == 5310.0

        client.submit_prompt(
            "session-1",
            "hello",
            request_timeout=7080.0,
        )
        assert client._request.call_args.kwargs["request_timeout"] == 7080.0

    def test_parsed_from_yaml(self, tmp_path, monkeypatch) -> None:
        config_dir = tmp_path
        (config_dir / "config.yaml").write_text(
            yaml.dump({
                "timeouts": {
                    "codespace_boot": 240,
                    "session_start": 30,
                    "session_new": 200,
                    "command": 600,
                }
            })
        )
        monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(config_dir))

        from agent_bridge.config import load_config

        cfg = load_config()
        assert cfg.timeouts.codespace_boot == 240
        assert cfg.timeouts.session_start == 30
        assert cfg.timeouts.session_new == 200
        assert cfg.timeouts.command == 600

    def test_partial_override_keeps_other_defaults(self) -> None:
        t = PhasedTimeouts(command=99)
        assert t.command == 99
        assert t.codespace_boot == PhasedTimeouts().codespace_boot
        # session_new keeps its default when not overridden (#2107).
        assert t.session_new == PhasedTimeouts().session_new

    def test_session_host_ready_default_and_parse(self, tmp_path, monkeypatch) -> None:
        # Local Session Host cold-start budget: present, positive, and larger
        # than the retired hard-coded 30s (which spuriously failed heavy /
        # elevated singleton launches).
        assert PhasedTimeouts().session_host_ready >= 60.0
        (tmp_path / "config.yaml").write_text(
            yaml.dump({"timeouts": {"session_host_ready": 150}})
        )
        monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
        from agent_bridge.config import load_config

        assert load_config().timeouts.session_host_ready == 150


class TestLocalSpawnerReadyTimeout:
    """The session_host_ready budget must reach launch_session_host."""

    def test_local_spawner_forwards_ready_timeout(self, monkeypatch) -> None:
        import asyncio
        from types import SimpleNamespace

        from agent_bridge.session_host import spawner as sp

        seen: dict = {}

        def _fake_launch(child_argv, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(
                host_pid=1, child_pid=2, port=3, protocol_version=1,
                state_file="x", proc=None,
            )

        monkeypatch.setattr(sp, "launch_session_host", _fake_launch)
        s = sp.LocalSpawner(ready_timeout=123.0)
        asyncio.run(s.spawn(["copilot", "--acp", "--stdio"], cwd="/w"))
        assert seen["ready_timeout"] == 123.0

    def test_local_spawner_default_ready_timeout_is_generous(self) -> None:
        from agent_bridge.session_host.spawner import LocalSpawner

        assert LocalSpawner()._ready_timeout >= 60.0
