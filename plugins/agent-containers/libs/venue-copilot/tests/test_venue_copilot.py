"""Tests for the shared venue-copilot reserve/connect/release orchestration."""
from __future__ import annotations

import json
import os
import shlex
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
    def test_builds_expected_argv_and_parses_reservation(self, monkeypatch) -> None:
        monkeypatch.setattr("venue_copilot.shutil.which", lambda name: None)
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

    def test_resolves_bridge_bin_via_path_when_available(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "venue_copilot.shutil.which",
            lambda name: r"C:\fake\agent-bridge.CMD" if name == "agent-bridge" else None,
        )
        seen: list[list[str]] = []

        def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
            seen.append(argv)
            return _FakeCompletedProcess(json.dumps({}))

        reserve_cli_mode("wt-A", run=fake_run)
        # A bare binstub name resolves via PATH to its real (extensioned) file
        # before spawning -- a direct list-argv subprocess spawn on Windows
        # never tries PATHEXT itself (confirmed live against a real
        # CodeSpace, agent-bridge-cli-mode-sessions Phase 4 validation).
        assert seen[0][0] == r"C:\fake\agent-bridge.CMD"

    def test_uses_custom_bridge_bin(self, monkeypatch) -> None:
        monkeypatch.setattr("venue_copilot.shutil.which", lambda name: None)
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
    def test_returns_removed_count(self, monkeypatch) -> None:
        monkeypatch.setattr("venue_copilot.shutil.which", lambda name: None)
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
        assert cmd == "bash -lc 'agent-worktrees copilot --worktree-id wt-A'"

    def test_with_driver_seed_and_ensure_mux(self) -> None:
        cmd = build_copilot_remote_command(
            "wt-A", driver="cli-mode", seed="do the thing", ensure_mux=True,
        )
        assert cmd == (
            "bash -lc 'agent-worktrees copilot --worktree-id wt-A --driver "
            "cli-mode --seed '\"'\"'do the thing'\"'\"' --ensure-mux'"
        )

    def test_custom_embody_bin_is_quoted_safely(self) -> None:
        cmd = build_copilot_remote_command(
            "wt-A", embody_bin="/opt/venv/bin/agent-worktrees", ensure_mux=False,
        )
        assert cmd.startswith("bash -lc '/opt/venv/bin/agent-worktrees copilot")

    def test_wrapped_in_login_shell_so_remote_path_is_sourced(self) -> None:
        # Confirmed live (agent-bridge-cli-mode-sessions Phase 4 validation,
        # a disposable trusted-container venue): OpenSSH's non-interactive
        # remote-command exec never sources ~/.profile/~/.bashrc, so a
        # bare (unwrapped) command resolves `agent-worktrees` to nothing even
        # when it is genuinely, fully installed under ~/.local/bin -- exit
        # 127, a distinct bug from the already-tracked "venue lacks a full
        # install" gap. `bash -lc` restores the login-shell PATH.
        cmd = build_copilot_remote_command("wt-A", ensure_mux=False)
        assert cmd.startswith("bash -lc ")
        inner = shlex.split(cmd)[2]
        assert inner == "agent-worktrees copilot --worktree-id wt-A"

    def test_anchor_forwards_anchor_flag_not_worktree_id(self) -> None:
        # A CodeSpace/container venue is conventionally anchor-only (no
        # worktree unless an operator explicitly created one) -- matching
        # headless ACP dispatch's own existing anchor-checkout behavior.
        # `worktree_id` is still the CLI-mode reservation identity (the
        # caller's synthesized `anchor-<repo_name>`), but must never reach
        # the remote command as a literal --worktree-id (there is no such
        # tracked worktree to resolve).
        cmd = build_copilot_remote_command(
            "anchor-odsp-web", anchor=True, ensure_mux=False,
        )
        assert cmd == "bash -lc 'agent-worktrees copilot --anchor'"
        assert "anchor-odsp-web" not in cmd

    def test_anchor_with_driver_and_seed(self) -> None:
        cmd = build_copilot_remote_command(
            "anchor-odsp-web", anchor=True, driver="cli-mode", seed="explore",
        )
        assert cmd == (
            "bash -lc 'agent-worktrees copilot --anchor --driver cli-mode "
            "--seed explore --ensure-mux'"
        )


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

    def test_anchor_mode_reserves_with_the_synthesized_identity_but_builds_the_anchor_command(
        self,
    ) -> None:
        calls: list[str] = []
        reserved_worktree_ids: list[str] = []

        def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
            calls.append(argv[4])
            if argv[4] == "reserve":
                reserved_worktree_ids.append(argv[argv.index("--worktree-id") + 1])
            return _FakeCompletedProcess(json.dumps({"removed": 1}))

        seen_command = {}

        def connect(remote_command: str) -> int:
            seen_command["cmd"] = remote_command
            return 0

        rc = run_venue_copilot(
            "anchor-odsp-web", connect=connect, anchor=True, run=fake_run,
        )
        assert rc == 0
        assert reserved_worktree_ids == ["anchor-odsp-web"]
        assert "--anchor" in seen_command["cmd"]
        assert "anchor-odsp-web" not in seen_command["cmd"]

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


class TestDetachedLaunchAdditions:
    """Venue CLI-mode detached launch: venue-carrying reservations, exact
    release, reservation status, and the `embody`-shaped remote command."""

    def _recording(self, payload):
        seen: list[list[str]] = []

        def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
            seen.append(argv)
            return _FakeCompletedProcess(json.dumps(payload))

        return seen, fake_run

    def test_reserve_forwards_compact_venue_json(self, monkeypatch) -> None:
        monkeypatch.setattr("venue_copilot.shutil.which", lambda name: None)
        seen, fake_run = self._recording({"reservation_id": "r1"})
        venue = {"kind": "codespace", "target": "cs-1", "mux_session_name": "wt-x"}

        reserve_cli_mode("x@cs-1", venue=venue, run=fake_run)

        argv = seen[0]
        assert argv[argv.index("--venue-json") + 1] == json.dumps(
            venue, separators=(",", ":"),
        )

    def test_release_by_exact_reservation_id(self, monkeypatch) -> None:
        monkeypatch.setattr("venue_copilot.shutil.which", lambda name: None)
        seen, fake_run = self._recording({"removed": 1})

        assert release_cli_mode("wt-A", reservation_id="r1", run=fake_run) == 1
        assert seen[0][-2:] == ["--reservation-id", "r1"]

    def test_get_reservation_reports_claimant(self, monkeypatch) -> None:
        from venue_copilot import get_cli_mode_reservation

        monkeypatch.setattr("venue_copilot.shutil.which", lambda name: None)
        seen, fake_run = self._recording(
            {"reservation_id": "r1", "claimed_by_session_id": "sid-9"},
        )

        got = get_cli_mode_reservation("wt-A", run=fake_run)

        assert got["claimed_by_session_id"] == "sid-9"
        assert seen[0][:5] == [
            "agent-bridge", "--json", "live-sessions", "cli-mode", "status",
        ]

    def test_detached_command_uses_embody_with_scope_and_copilot_args(self) -> None:
        cmd = build_copilot_remote_command(
            "anchor-example@cs-1", anchor=True, driver="orchestrator",
            seed="do it", detach=True, bridge_scope_id="anchor-example@cs-1",
            copilot_args=["--plugin-dir=/stage/a", "--no-ask-user"],
            login_shell=False,
        )
        argv = shlex.split(cmd)
        assert argv[:3] == ["agent-worktrees", "embody", "--anchor"]
        assert argv[argv.index("--bridge-scope-id") + 1] == "anchor-example@cs-1"
        assert "--copilot-arg=--plugin-dir=/stage/a" in argv
        assert "--copilot-arg=--no-ask-user" in argv
        assert argv[-1] == "--json"
        assert "--worktree-id" not in argv

    @pytest.mark.parametrize("login_shell", [False, True])
    def test_staged_home_plugin_dirs_expand_in_the_remote_shell(self, login_shell: bool) -> None:
        import shutil
        import subprocess

        bash = shutil.which("bash")
        if bash is None or os.name == "nt":
            pytest.skip("needs a POSIX bash")
        cmd = build_copilot_remote_command(
            "anchor-example@cs-1", anchor=True, detach=True,
            seed="keep $HOME literal", driver="d",
            copilot_args=["--plugin-dir=$HOME/.stage/a b", "--no-ask-user"],
            login_shell=login_shell,
        )
        # The remote shell expands $HOME only inside --copilot-arg values; the
        # seed text stays verbatim.
        script = f"embody_argv() {{ printf '%s\\n' \"$@\"; }}; HOME=/h/u; {cmd}"
        script = script.replace("agent-worktrees", "embody_argv", 1)
        if login_shell:
            script = script.replace("bash -lc ", "bash -c ", 1)
            script = f"export -f embody_argv 2>/dev/null; {script}"
        out = subprocess.run(
            [bash, "--noprofile", "--norc", "-c", script],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "HOME": "/h/u"},
        ).stdout.splitlines()
        assert "--copilot-arg=--plugin-dir=/h/u/.stage/a b" in out
        assert "keep $HOME literal" in out

    def test_attached_command_is_unchanged_by_default(self) -> None:
        cmd = build_copilot_remote_command("wt-A", anchor=True, seed="s")
        assert cmd.startswith("bash -lc ")
        inner = shlex.split(cmd)[2]
        assert shlex.split(inner)[:3] == ["agent-worktrees", "copilot", "--anchor"]
        assert "--json" not in inner
