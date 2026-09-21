"""Tests for the shared venue-copilot reserve/connect/release orchestration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from venue_copilot import (
    VenueCopilotError,
    build_copilot_remote_command,
    daemon_port_reverse_forward,
    release_cli_mode,
    reserve_cli_mode,
    resolve_daemon_port,
    run_venue_copilot,
)


class _FakeCompletedProcess:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _run_returning(payload: dict[str, Any], returncode: int = 0):
    def _run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(json.dumps(payload), returncode)
    return _run


class TestReserveCliMode:
    def test_builds_expected_argv_and_parses_reservation(self) -> None:
        seen: list[list[str]] = []

        def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
            seen.append(argv)
            return _FakeCompletedProcess(json.dumps({"reservation_id": "r1"}))

        result = reserve_cli_mode("wt-A", ttl_seconds=60.0, run=fake_run)
        assert result == {"reservation_id": "r1"}
        assert seen == [[
            "agent-bridge", "--json", "live-sessions", "cli-mode", "reserve",
            "--worktree-id", "wt-A", "--ttl-seconds", "60.0",
        ]]

    def test_uses_custom_bridge_bin(self) -> None:
        seen: list[list[str]] = []

        def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
            seen.append(argv)
            return _FakeCompletedProcess(json.dumps({}))

        reserve_cli_mode("wt-A", bridge_bin="agent-bridge.exe", run=fake_run)
        assert seen[0][0] == "agent-bridge.exe"

    def test_raises_on_error_payload_even_with_zero_exit(self) -> None:
        with pytest.raises(VenueCopilotError, match="already has an active"):
            reserve_cli_mode(
                "wt-A",
                run=_run_returning({"error": "already has an active reservation"}),
            )

    def test_raises_on_nonzero_exit_without_json(self) -> None:
        def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
            return _FakeCompletedProcess("", returncode=1, stderr="boom")

        with pytest.raises(VenueCopilotError, match="boom"):
            reserve_cli_mode("wt-A", run=fake_run)


class TestReleaseCliMode:
    def test_returns_removed_count(self) -> None:
        seen: list[list[str]] = []

        def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
            seen.append(argv)
            return _FakeCompletedProcess(json.dumps({"removed": 1}))

        assert release_cli_mode("wt-A", run=fake_run) == 1
        assert seen == [[
            "agent-bridge", "--json", "live-sessions", "cli-mode", "release",
            "--worktree-id", "wt-A",
        ]]

    def test_never_raises_on_failure(self) -> None:
        def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
            return _FakeCompletedProcess("", returncode=1, stderr="daemon unreachable")

        assert release_cli_mode("wt-A", run=fake_run) == 0


class TestBuildCopilotRemoteCommand:
    def test_minimal(self) -> None:
        cmd = build_copilot_remote_command("wt-A", ensure_mux=False)
        assert cmd == "agent-worktrees copilot --worktree-id wt-A"

    def test_with_driver_seed_and_ensure_mux(self) -> None:
        cmd = build_copilot_remote_command(
            "wt-A", driver="cli-mode", seed="do the thing", ensure_mux=True,
        )
        assert cmd == (
            "agent-worktrees copilot --worktree-id wt-A --driver cli-mode "
            "--seed 'do the thing' --ensure-mux"
        )

    def test_custom_embody_bin_is_quoted_safely(self) -> None:
        cmd = build_copilot_remote_command(
            "wt-A", embody_bin="/opt/venv/bin/agent-worktrees", ensure_mux=False,
        )
        assert cmd.startswith("/opt/venv/bin/agent-worktrees copilot")


class TestRunVenueCopilot:
    def test_reserves_connects_and_releases_on_success(self) -> None:
        calls: list[str] = []

        def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
            calls.append(argv[4])  # the cli-mode sub-action
            return _FakeCompletedProcess(json.dumps({"removed": 1}))

        seen_command = {}

        def connect(remote_command: str) -> int:
            seen_command["cmd"] = remote_command
            calls.append("connect")
            return 0

        rc = run_venue_copilot("wt-A", connect=connect, run=fake_run)
        assert rc == 0
        assert calls == ["reserve", "connect", "release"]
        assert "agent-worktrees copilot --worktree-id wt-A" in seen_command["cmd"]

    def test_releases_even_when_connect_raises(self) -> None:
        calls: list[str] = []

        def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
            calls.append(argv[4])
            return _FakeCompletedProcess(json.dumps({"removed": 1}))

        def connect(remote_command: str) -> int:
            raise RuntimeError("ssh dropped")

        with pytest.raises(RuntimeError, match="ssh dropped"):
            run_venue_copilot("wt-A", connect=connect, run=fake_run)
        assert calls == ["reserve", "release"]

    def test_connect_never_called_when_reserve_fails(self) -> None:
        def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
            return _FakeCompletedProcess(json.dumps({"error": "already reserved"}))

        called = {"connect": False}

        def connect(remote_command: str) -> int:
            called["connect"] = True
            return 0

        with pytest.raises(VenueCopilotError):
            run_venue_copilot("wt-A", connect=connect, run=fake_run)
        assert called["connect"] is False


class TestResolveDaemonPort:
    def test_reads_active_port_from_routing_table(self, tmp_path: Path) -> None:
        table = {"active": {"bind": "127.0.0.1", "port": 9280, "generation": 1}}
        (tmp_path / "active.json").write_text(json.dumps(table), encoding="utf-8")
        assert resolve_daemon_port(str(tmp_path)) == 9280

    def test_returns_none_when_table_absent(self, tmp_path: Path) -> None:
        assert resolve_daemon_port(str(tmp_path / "missing")) is None

    def test_env_var_used_when_config_dir_omitted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        table = {"active": {"bind": "127.0.0.1", "port": 4242, "generation": 1}}
        (tmp_path / "active.json").write_text(json.dumps(table), encoding="utf-8")
        monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
        assert resolve_daemon_port() == 4242


class TestDaemonPortReverseForward:
    def test_builds_loopback_to_loopback_spec(self) -> None:
        assert daemon_port_reverse_forward(9280) == "9280:127.0.0.1:9280"
