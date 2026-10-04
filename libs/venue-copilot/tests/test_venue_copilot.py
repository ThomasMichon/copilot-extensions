"""Tests for the shared venue-copilot reserve/connect/release orchestration."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

import pytest

from venue_copilot import (
    VenueCopilotError,
    bridge_probe_script,
    build_copilot_remote_command,
    daemon_port_reverse_forward,
    last_json,
    observe_commands,
    release_cli_mode,
    reserve_cli_mode,
    registration_credentials_script,
    reserve_with_retry,
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

    def test_a_wedged_bridge_call_is_bounded(self) -> None:
        import subprocess

        seen: dict[str, Any] = {}

        def fake_run(argv: list[str], **kwargs: Any) -> _FakeCompletedProcess:
            seen["timeout"] = kwargs.get("timeout")
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

        with pytest.raises(VenueCopilotError, match="did not answer"):
            reserve_cli_mode("wt-A", run=fake_run)
        assert seen["timeout"] and seen["timeout"] > 0


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
            "anchor-example-web", anchor=True, ensure_mux=False,
        )
        assert cmd == "bash -lc 'agent-worktrees copilot --anchor'"
        assert "anchor-example-web" not in cmd

    def test_anchor_with_driver_and_seed(self) -> None:
        cmd = build_copilot_remote_command(
            "anchor-example-web", anchor=True, driver="cli-mode", seed="explore",
        )
        assert cmd == (
            "bash -lc 'agent-worktrees copilot --anchor --driver cli-mode "
            "--seed explore --ensure-mux'"
        )

    def test_seed_ready_timeout_is_forwarded_for_seeded_launch(self) -> None:
        cmd = build_copilot_remote_command(
            "wt-A",
            seed="do the thing",
            seed_ready_timeout=600.0,
        )
        assert "--seed-ready-timeout 600.0" in cmd


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
            "anchor-example-web", connect=connect, anchor=True, run=fake_run,
        )
        assert rc == 0
        assert reserved_worktree_ids == ["anchor-example-web"]
        assert "--anchor" in seen_command["cmd"]
        assert "anchor-example-web" not in seen_command["cmd"]

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
    def test_staged_home_plugin_dirs_expand_in_the_remote_shell(
        self, login_shell: bool, tmp_path: Path,
    ) -> None:
        import shutil
        import subprocess

        bash = shutil.which("bash")
        if bash is None or os.name == "nt":
            pytest.skip("needs a POSIX bash")
        bindir = tmp_path / "bin"
        bindir.mkdir()
        stub = bindir / "agent-worktrees"
        stub.write_text(
            "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        cmd = build_copilot_remote_command(
            "anchor-example@cs-1", anchor=True, detach=True,
            seed="keep $HOME literal", driver="d",
            copilot_args=["--plugin-dir=$HOME/.stage/a b", "--no-ask-user"],
            embody_bin=str(stub),
            login_shell=login_shell,
        )
        # The remote shell expands $HOME only inside --copilot-arg values; the
        # seed text stays verbatim.
        script = f"HOME=/h/u; {cmd}"
        out = subprocess.run(
            [bash, "--noprofile", "--norc", "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": "/h/u"},
        ).stdout.splitlines()
        assert "--copilot-arg=--plugin-dir=/h/u/.stage/a b" in out
        assert "keep $HOME literal" in out

    def test_attached_command_is_unchanged_by_default(self) -> None:
        cmd = build_copilot_remote_command("wt-A", anchor=True, seed="s")
        assert cmd.startswith("bash -lc ")
        inner = shlex.split(cmd)[2]
        assert shlex.split(inner)[:3] == ["agent-worktrees", "copilot", "--anchor"]
        assert "--json" not in inner


class TestDetachedSharedHelpers:
    def test_last_json_returns_the_last_object(self) -> None:
        assert last_json('noise {"a": 1}\n{"b": 2}') == {"b": 2}
        assert last_json("no json") == {}

    def test_reserve_with_retry_waits_on_active_reservation(self, monkeypatch) -> None:
        calls = []
        sleeps = []

        def fake_reserve(scope, *, ttl_seconds, venue, bridge_bin="agent-bridge", run=None):
            calls.append((scope, ttl_seconds, venue))
            if len(calls) == 1:
                raise VenueCopilotError("reservation_active")
            return {"reservation_id": "r2"}

        monkeypatch.setattr("venue_copilot.reserve_cli_mode", fake_reserve)
        monkeypatch.setattr("venue_copilot.time.sleep", lambda delay: sleeps.append(delay))
        waits = []
        got = reserve_with_retry(
            "scope",
            {"kind": "ssh", "target": "host", "mux_session_name": "wt-x"},
            ttl=42,
            retry_window=60,
            on_wait=lambda: waits.append("wait"),
        )
        assert got == {"reservation_id": "r2"}
        assert len(calls) == 2 and calls[0][1] == 42
        assert waits == ["wait"] and sleeps == [5.0]

    def test_bridge_probe_script_and_observe_commands(self) -> None:
        script = bridge_probe_script(41234)
        assert "auth.yaml" in script
        assert "http://127.0.0.1:41234/api/v1/live-sessions" in script
        assert observe_commands("sid-1") == {
            "status": "agent-bridge --json live-sessions resolve --handle sid-1",
            "observe": "agent-bridge result sid-1 --json --max-items 5 --max-text-chars 2000",
            "nudge": 'agent-bridge send sid-1 "<message>" --no-wait',
        }


class _Adapter:
    def __init__(
        self,
        launch_payload: dict[str, Any] | None = None,
        *,
        hold_added: bool = True,
    ) -> None:
        self.launch_payload = launch_payload or {
            "ok": True,
            "created": True,
            "seed_submitted": True,
            "session": "wt-anchor-repo",
        }
        self.hold_added = hold_added
        self.calls: list[tuple[str, str]] = []
        self.keeper_stopped = False
        self.stop_keeper_error: Exception | None = None

    def run(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        self.calls.append(("run", command))
        if "curl" in command:
            return 0, "", ""
        if "tmux kill-session" in command:
            return 0, "STOPPED\n", ""
        return 0, "", ""

    def launch(self, command: str, *, timeout: float) -> tuple[int, str, str]:
        self.calls.append(("launch", command))
        return 0, json.dumps(self.launch_payload), ""

    def run_input(self, command: str, stdin: bytes, *, timeout: float) -> tuple[int, str, str]:
        self.calls.append(("run_input", command))
        self.stdin = stdin
        return (0, "/home/u/.agent-bridge/refs/b1\n", "") if getattr(self, "refs_ok", True) else (1, "", "denied")

    def ensure_keeper(self, *, venue_port: int, mux: str) -> dict[str, Any]:
        self.calls.append(("keeper", f"{venue_port}:{mux}"))
        return {"started": True, "hold_added": self.hold_added, "state": {"pid": 123}}

    def stop_keeper(self) -> bool:
        if self.stop_keeper_error is not None:
            raise self.stop_keeper_error
        self.keeper_stopped = True
        return True

    def attach_command(self, plan: dict[str, Any]) -> str:
        return f"attach {plan['mux_session']}"

    def stop_command(self, plan: dict[str, Any]) -> str:
        return f"stop {plan['mux_session']}"


class TestDetachedRunner:
    def _plan(self) -> dict[str, Any]:
        return {
            "identity": "anchor-repo",
            "scope_id": "anchor-repo@venue",
            "mux_session": "wt-anchor-repo",
            "workspace": "/workspaces/repo",
            "anchor": True,
            "venue": {"kind": "ssh", "target": "venue", "mux_session_name": "wt-anchor-repo"},
        }

    def test_launch_detached_happy_path(self, monkeypatch) -> None:
        from venue_copilot import detached

        adapter = _Adapter()
        monkeypatch.setattr("venue_copilot.detached.resolve_daemon_port", lambda: 41234)
        monkeypatch.setattr("venue_copilot.detached.resolve_local_auth_token", lambda: "tok")
        monkeypatch.setattr(
            "venue_copilot.detached.reserve_with_retry",
            lambda scope, venue, **kw: {"reservation_id": "r1"},
        )
        monkeypatch.setattr("venue_copilot.detached.await_claim", lambda scope, rid, timeout: "sid-42")
        released = []
        monkeypatch.setattr(
            "venue_copilot.detached.release_cli_mode",
            lambda scope, reservation_id=None: released.append((scope, reservation_id)) or 1,
        )

        rc, payload = detached.launch_detached(
            adapter,
            self._plan(),
            seed="do it",
            driver="orchestrator",
            copilot_args=["--no-ask-user"],
            ensure_mux=True,
            register_timeout=0.0,
            progress=lambda *a: None,
        )

        assert rc == 0
        assert payload["session_id"] == "sid-42"
        assert payload["commands"] == {
            "status": "agent-bridge --json live-sessions resolve --handle sid-42",
            "observe": "agent-bridge result sid-42 --json --max-items 5 --max-text-chars 2000",
            "nudge": 'agent-bridge send sid-42 "<message>" --no-wait',
            "attach": "attach wt-anchor-repo",
            "stop": "stop wt-anchor-repo",
        }
        assert any("auth.yaml" in command for kind, command in adapter.calls if kind == "run")
        launch = next(command for kind, command in adapter.calls if kind == "launch")
        assert "cd /workspaces/repo" in launch
        assert "--bridge-scope-id anchor-repo@venue" in launch
        assert "--copilot-arg=--no-ask-user" in launch
        assert released == [("anchor-repo@venue", "r1")]

    def _launch_resume(self, monkeypatch, protocol: int | None) -> tuple[int, dict[str, Any], list]:
        from venue_copilot import detached

        def health(_port):
            if protocol is None:
                raise OSError("connection refused")
            return {"protocol_version": protocol}

        monkeypatch.setattr("venue_copilot._daemon_health", health)
        monkeypatch.setattr("venue_copilot.detached.resolve_daemon_port", lambda: 41234)
        monkeypatch.setattr("venue_copilot.detached.resolve_local_auth_token", lambda: "tok")
        monkeypatch.setattr(
            "venue_copilot.detached.reserve_with_retry",
            lambda scope, venue, **kw: {"reservation_id": "r1"},
        )
        monkeypatch.setattr("venue_copilot.detached.await_claim", lambda scope, rid, timeout: "sid-42")
        monkeypatch.setattr("venue_copilot.detached.release_cli_mode", lambda *a, **k: 1)
        adapter = _Adapter()
        adapter.launch_payload = {**adapter.launch_payload, "created": False}
        steps: list = []
        rc, payload = detached.launch_detached(
            adapter, self._plan(), seed=None, driver=None,
            copilot_args=["--resume=abc"], ensure_mux=True, register_timeout=0.0,
            progress=lambda *a: steps.append(a),
        )
        return rc, payload, steps

    @pytest.mark.parametrize("protocol", [19, None])
    def test_a_resume_on_a_daemon_without_aliases_reports_a_provisional_handle(
        self, monkeypatch, protocol
    ) -> None:
        """The alias floor is checked before launch, not only when a note is sent:
        a resume with nothing to deliver must not report a normal handle."""
        rc, payload, steps = self._launch_resume(monkeypatch, protocol)
        assert rc == 0
        assert payload["session_handle"] == "provisional"
        assert "live-session aliases" in payload["handle_warning"]
        assert steps[0][0] == "handle"  # reported before anything was reserved or launched

    def test_a_resume_on_an_alias_capable_daemon_reports_a_normal_handle(self, monkeypatch) -> None:
        rc, payload, steps = self._launch_resume(monkeypatch, 21)
        assert rc == 0
        assert "session_handle" not in payload and "handle_warning" not in payload
        assert not any(step[0] == "handle" for step in steps)

    def test_a_fresh_launch_never_asks_the_daemon(self) -> None:
        from venue_copilot import unstable_handle_warning

        def health(_port):
            raise AssertionError("a launch that can't switch ids needs no protocol check")

        assert unstable_handle_warning(41234, ["--no-ask-user"], health=health) is None

    def test_handle_never_echoes_runner_configuration(self, monkeypatch) -> None:
        from venue_copilot import detached

        monkeypatch.setattr("venue_copilot.detached.resolve_daemon_port", lambda: 41234)
        monkeypatch.setattr("venue_copilot.detached.resolve_local_auth_token", lambda: "tok")
        monkeypatch.setattr(
            "venue_copilot.detached.reserve_with_retry",
            lambda scope, venue, **kw: {"reservation_id": "r1"},
        )
        monkeypatch.setattr("venue_copilot.detached.await_claim", lambda scope, rid, timeout: "sid-42")
        monkeypatch.setattr("venue_copilot.detached.release_cli_mode", lambda *a, **k: 1)
        plan = {**self._plan(), "registration_error": "x", "reservation_ttl": 5.0, "launch_detail": "y"}
        rc, payload = detached.launch_detached(
            _Adapter(), plan, seed="do it", driver=None, copilot_args=[], ensure_mux=True,
            register_timeout=0.0, progress=lambda *a: None,
        )
        assert rc == 0
        assert not {"registration_error", "reservation_ttl", "launch_detail"} & payload.keys()
        assert payload["scope_id"] == "anchor-repo@venue"
        assert detached.public_plan(plan) == self._plan()

    def test_reservation_outlives_the_launch_and_registration_budget(self, monkeypatch) -> None:
        """A concurrent rejoin can replace an expired reservation; the TTL covers
        the refs upload, the seeded launch and registration."""
        from venue_copilot import detached

        self._patch_bridge(monkeypatch)
        ttls: list[float] = []
        monkeypatch.setattr(
            "venue_copilot.detached.reserve_with_retry",
            lambda scope, venue, **kw: ttls.append(kw["ttl_seconds"]) or {"reservation_id": "r1"},
        )
        rc, _ = detached.launch_detached(
            _Adapter(), self._plan(), seed="do it", driver=None, copilot_args=[],
            ensure_mux=True, register_timeout=600.0, progress=lambda *a: None, refs=self._refs(),
        )
        assert rc == 0
        launch = detached._SEED_READY_HARD_CAP + 300.0
        assert ttls == [600.0 + launch + 600.0 + 120.0]

    def _patch_bridge(self, monkeypatch) -> None:
        monkeypatch.setattr("venue_copilot.detached.resolve_daemon_port", lambda: 41234)
        monkeypatch.setattr("venue_copilot.detached.resolve_local_auth_token", lambda: "tok")
        monkeypatch.setattr(
            "venue_copilot.detached.reserve_with_retry",
            lambda scope, venue, **kw: {"reservation_id": "r1"},
        )
        monkeypatch.setattr("venue_copilot.detached.await_claim", lambda scope, rid, timeout: "sid-42")
        monkeypatch.setattr("venue_copilot.detached.release_cli_mode", lambda *a, **k: 1)

    def _refs(self) -> tuple[str, bytes, list[tuple[str, int]]]:
        return "mkdir -p x && base64 -d | tar -xzf - -C x && cd x && pwd", b"UEFZTE9BRA==", [("trace.har", 2048)]

    def test_ref_files_are_copied_before_launch_and_named_in_a_new_sessions_seed(self, monkeypatch) -> None:
        from venue_copilot import detached

        self._patch_bridge(monkeypatch)
        adapter = _Adapter()
        rc, payload = detached.launch_detached(
            adapter, self._plan(), seed="look at the trace", driver=None, copilot_args=[],
            ensure_mux=True, register_timeout=0.0, progress=lambda *a: None, refs=self._refs(),
        )
        assert rc == 0
        kinds = [kind for kind, _ in adapter.calls]
        assert kinds.index("run_input") < kinds.index("launch")
        assert adapter.stdin == b"UEFZTE9BRA=="
        launch = next(command for kind, command in adapter.calls if kind == "launch")
        assert "/home/u/.agent-bridge/refs/b1/trace.har" in launch
        assert payload["refs_delivered"] == "seed"
        assert payload["ref_files"] == ["- /home/u/.agent-bridge/refs/b1/trace.har (2.0 KB)"]

    def test_ref_files_for_a_running_session_are_sent_as_a_message(self, monkeypatch) -> None:
        from venue_copilot import detached, refs

        self._patch_bridge(monkeypatch)
        sent = []
        monkeypatch.setattr(refs, "deliver_note",
                            lambda sid, note, **kw: sent.append((sid, note, kw.get("operation"))) or True)
        adapter = _Adapter({"ok": True, "created": False, "session": "wt-anchor-repo"})
        rc, payload = detached.launch_detached(
            adapter, self._plan(), seed=None, driver=None, copilot_args=[],
            ensure_mux=True, register_timeout=0.0, progress=lambda *a: None, refs=self._refs(),
        )
        assert rc == 0
        assert payload["refs_delivered"] == "message"
        assert sent and sent[0][0] == "sid-42" and "trace.har" in sent[0][1]
        assert sent[0][2] == "r1"  # keyed to this launch: a later launch's same note is sent again

    @pytest.mark.parametrize("daemon_has_aliases", [True, False])
    def test_a_resumed_rejoins_ref_note_needs_a_daemon_that_follows_renames(
        self, monkeypatch, daemon_has_aliases,
    ) -> None:
        from venue_copilot import detached, refs

        self._patch_bridge(monkeypatch)
        sent = []
        monkeypatch.setattr(
            refs, "deliver_note", lambda sid, note, **kw: sent.append({k: v for k, v in kw.items() if k != "operation"}) or daemon_has_aliases,
        )
        adapter = _Adapter({"ok": True, "created": False, "session": "wt-anchor-repo"})
        rc, payload = detached.launch_detached(
            adapter, self._plan(), seed=None, driver=None, copilot_args=["--resume=abc"],
            ensure_mux=True, register_timeout=0.0, progress=lambda *a: None, refs=self._refs(),
        )
        assert rc == 0
        assert sent == [{"min_daemon_protocol": detached.LIVE_SESSION_ALIAS_PROTOCOL}]
        assert payload["refs_delivered"] == ("message" if daemon_has_aliases else "failed")

    def test_a_failed_ref_copy_fails_before_launch(self, monkeypatch) -> None:
        from venue_copilot import detached

        self._patch_bridge(monkeypatch)
        adapter = _Adapter()
        adapter.refs_ok = False
        rc, payload = detached.launch_detached(
            adapter, self._plan(), seed="x", driver=None, copilot_args=[],
            ensure_mux=True, register_timeout=0.0, progress=lambda *a: None, refs=self._refs(),
        )
        assert rc == 1
        assert "reference files" in payload["error"]
        assert "launch" not in [kind for kind, _ in adapter.calls]

    def test_unsubmitted_seed_on_registered_session_is_delivered_over_bridge(self, monkeypatch) -> None:
        from venue_copilot import detached, refs

        adapter = _Adapter({"ok": True, "created": True, "seed_submitted": False})
        sent = []
        monkeypatch.setattr("venue_copilot.detached.resolve_daemon_port", lambda: 41234)
        monkeypatch.setattr("venue_copilot.detached.resolve_local_auth_token", lambda: "tok")
        monkeypatch.setattr(
            "venue_copilot.detached.reserve_with_retry",
            lambda scope, venue, **kw: {"reservation_id": "r1"},
        )
        monkeypatch.setattr("venue_copilot.detached.await_claim", lambda scope, rid, timeout: "sid-42")
        monkeypatch.setattr(refs, "deliver_note", lambda sid, note, **kw: sent.append((sid, note)) or True)
        monkeypatch.setattr("venue_copilot.detached.release_cli_mode", lambda *a, **k: 1)

        rc, payload = detached.launch_detached(
            adapter,
            self._plan(),
            seed="do it",
            driver="d",
            copilot_args=[],
            ensure_mux=True,
            register_timeout=0.0,
            progress=lambda *a: None,
        )

        assert rc == 0
        assert payload["session_id"] == "sid-42"
        assert payload["seed_delivery"] == "bridge"
        assert payload["seeded"] is True
        assert sent == [("sid-42", "do it")]
        assert adapter.keeper_stopped is False
        assert not any("tmux kill-session" in command for kind, command in adapter.calls if kind == "run")

    @pytest.mark.parametrize("reason", ["enter-failed", "seed-not-echoed"])
    def test_a_typed_but_unsubmitted_seed_is_never_resent(self, monkeypatch, reason) -> None:
        """The draft may still sit in Copilot's input: a bridge copy could run the
        task twice, so the launch keeps the session and reports the seed failed."""
        from venue_copilot import detached, refs

        adapter = _Adapter({"ok": True, "created": True, "seeded": True,
                            "seed_submitted": False, "seed_reason": reason})
        sent = []
        monkeypatch.setattr("venue_copilot.detached.resolve_daemon_port", lambda: 41234)
        monkeypatch.setattr("venue_copilot.detached.resolve_local_auth_token", lambda: "tok")
        monkeypatch.setattr(
            "venue_copilot.detached.reserve_with_retry",
            lambda scope, venue, **kw: {"reservation_id": "r1"},
        )
        monkeypatch.setattr("venue_copilot.detached.await_claim", lambda scope, rid, timeout: "sid-42")
        monkeypatch.setattr(refs, "deliver_note", lambda *a, **k: sent.append(a) or True)
        monkeypatch.setattr("venue_copilot.detached.release_cli_mode", lambda *a, **k: 1)

        rc, payload = detached.launch_detached(
            adapter, self._plan(), seed="do it", driver="d", copilot_args=[],
            ensure_mux=True, register_timeout=0.0, progress=lambda *a: None,
        )

        assert rc == 0 and payload["session_id"] == "sid-42"
        assert payload["seed_delivery"] == "failed"
        assert payload["seeded"] is False
        assert sent == []
        assert adapter.keeper_stopped is False

    def test_seed_outcome_matrix(self) -> None:
        from venue_copilot import seed_outcome

        assert seed_outcome({"seed_submitted": True}, created=True, seed="s") == ("typed", False)
        assert seed_outcome({"seeded": True}, created=True, seed="s") == ("failed", False)
        assert seed_outcome({"seeded": False}, created=True, seed="s") == (None, True)
        assert seed_outcome({}, created=True, seed="s") == (None, True)
        assert seed_outcome({}, created=False, seed="s") == (None, False)
        assert seed_outcome({}, created=True, seed=None) == (None, False)
        # Only a reason proving nothing was typed may fall back to the bridge.
        for safe in ("not-ready-timeout", "pane-target-unresolved"):
            assert seed_outcome({"seeded": False, "seed_reason": safe}, created=True, seed="s") == (None, True)
        for unsure in ("send-failed", "seed-not-echoed", "enter-failed"):
            assert seed_outcome({"seeded": False, "seed_reason": unsure}, created=True, seed="s") == ("failed", False)

    def test_unsubmitted_seed_without_registration_stops_created_session(self, monkeypatch) -> None:
        from venue_copilot import detached

        adapter = _Adapter({"ok": True, "created": True, "seed_submitted": False})
        monkeypatch.setattr("venue_copilot.detached.resolve_daemon_port", lambda: 41234)
        monkeypatch.setattr("venue_copilot.detached.resolve_local_auth_token", lambda: "tok")
        monkeypatch.setattr(
            "venue_copilot.detached.reserve_with_retry",
            lambda scope, venue, **kw: {"reservation_id": "r1"},
        )
        monkeypatch.setattr("venue_copilot.detached.await_claim", lambda scope, rid, timeout: None)
        monkeypatch.setattr("venue_copilot.detached.release_cli_mode", lambda *a, **k: 1)

        rc, payload = detached.launch_detached(
            adapter,
            self._plan(),
            seed="do it",
            driver="d",
            copilot_args=[],
            ensure_mux=True,
            register_timeout=0.0,
            progress=lambda *a: None,
        )

        assert rc == 1
        assert "never registered" in payload["error"]
        assert adapter.keeper_stopped is True
        assert any("tmux kill-session" in command for kind, command in adapter.calls if kind == "run")

    def test_launch_failure_does_not_release_preexisting_keeper_hold(self, monkeypatch) -> None:
        from venue_copilot import detached

        adapter = _Adapter(
            {"ok": True, "created": False, "session": "wt-anchor-repo"},
            hold_added=False,
        )
        monkeypatch.setattr("venue_copilot.detached.resolve_daemon_port", lambda: 41234)
        monkeypatch.setattr("venue_copilot.detached.resolve_local_auth_token", lambda: "tok")
        monkeypatch.setattr(
            "venue_copilot.detached.reserve_with_retry",
            lambda scope, venue, **kw: {"reservation_id": "r1"},
        )
        monkeypatch.setattr("venue_copilot.detached.await_claim", lambda scope, rid, timeout: None)
        monkeypatch.setattr("venue_copilot.detached.release_cli_mode", lambda *a, **k: 1)

        rc, payload = detached.launch_detached(
            adapter,
            self._plan(),
            seed="do it",
            driver="d",
            copilot_args=[],
            ensure_mux=True,
            register_timeout=0.0,
            progress=lambda *a: None,
        )

        assert rc == 1
        assert "never registered" in payload["error"]
        assert adapter.keeper_stopped is False
        assert not any("tmux kill-session" in command for kind, command in adapter.calls if kind == "run")

    def test_launch_failure_cleanup_preserves_original_payload_when_keeper_stop_fails(
        self, monkeypatch, capsys,
    ) -> None:
        from venue_copilot import detached

        adapter = _Adapter({"ok": True, "created": True, "seed_submitted": False})
        adapter.stop_keeper_error = RuntimeError("lock busy")
        monkeypatch.setattr("venue_copilot.detached.resolve_daemon_port", lambda: 41234)
        monkeypatch.setattr("venue_copilot.detached.resolve_local_auth_token", lambda: "tok")
        monkeypatch.setattr(
            "venue_copilot.detached.reserve_with_retry",
            lambda scope, venue, **kw: {"reservation_id": "r1"},
        )
        monkeypatch.setattr("venue_copilot.detached.release_cli_mode", lambda *a, **k: 1)
        # A real launch failure: the created session never registers.
        monkeypatch.setattr("venue_copilot.detached.await_claim", lambda scope, rid, timeout: None)

        rc, payload = detached.launch_detached(
            adapter,
            self._plan(),
            seed="do it",
            driver="d",
            copilot_args=[],
            ensure_mux=True,
            register_timeout=0.0,
            progress=lambda *a: None,
        )

        assert rc == 1
        assert "never registered" in payload["error"]  # the original error, not the keeper's
        assert "could not update the forward keeper" in capsys.readouterr().err
        assert any("tmux kill-session" in command for kind, command in adapter.calls if kind == "run")

    def test_detached_launch_uses_180_second_seed_ready_timeout_floor(self, monkeypatch) -> None:
        from venue_copilot import detached

        self._patch_bridge(monkeypatch)
        adapter = _Adapter()
        launch_timeouts = []
        original_launch = adapter.launch

        def launch(command: str, *, timeout: float) -> tuple[int, str, str]:
            launch_timeouts.append(timeout)
            return original_launch(command, timeout=timeout)

        adapter.launch = launch

        rc, _payload = detached.launch_detached(
            adapter,
            self._plan(),
            seed="do it",
            driver=None,
            copilot_args=[],
            ensure_mux=True,
            register_timeout=30.0,
            progress=lambda *a: None,
        )

        assert rc == 0
        launch = next(command for kind, command in adapter.calls if kind == "launch")
        assert "--seed-ready-timeout 180.0" in launch
        assert launch_timeouts == [1200.0]

    @pytest.mark.parametrize("anchor, expected", [(False, 1200.0), (True, 330.0)])
    def test_a_worktree_launch_without_a_seed_still_budgets_for_its_pending_seed(
        self, monkeypatch, anchor, expected,
    ) -> None:
        """A worktree launch can consume the worktree's own pending seed with no
        seed or refs passed here; its readiness wait can reach the hard cap, so
        the transport budget gets the same floor. An anchor launch has none."""
        from venue_copilot import detached

        self._patch_bridge(monkeypatch)
        adapter = _Adapter()
        launch_timeouts = []
        original_launch = adapter.launch

        def launch(command: str, *, timeout: float) -> tuple[int, str, str]:
            launch_timeouts.append(timeout)
            return original_launch(command, timeout=timeout)

        adapter.launch = launch
        rc, _payload = detached.launch_detached(
            adapter,
            {**self._plan(), "anchor": anchor},
            seed=None,
            driver=None,
            copilot_args=[],
            ensure_mux=True,
            register_timeout=30.0,
            progress=lambda *a: None,
        )

        assert rc == 0
        assert launch_timeouts == [expected]

    def test_detached_launch_uses_register_timeout_when_larger(self, monkeypatch) -> None:
        from venue_copilot import detached

        self._patch_bridge(monkeypatch)
        adapter = _Adapter()

        rc, _payload = detached.launch_detached(
            adapter,
            self._plan(),
            seed="do it",
            driver=None,
            copilot_args=[],
            ensure_mux=True,
            register_timeout=600.0,
            progress=lambda *a: None,
        )

        assert rc == 0
        launch = next(command for kind, command in adapter.calls if kind == "launch")
        assert "--seed-ready-timeout 600.0" in launch

    def test_stop_detached_verifies_releases_and_deregisters(self, monkeypatch) -> None:
        from venue_copilot import detached

        adapter = _Adapter()
        released = []
        deregistered = []
        monkeypatch.setattr(
            "venue_copilot.detached.release_cli_mode",
            lambda scope, reservation_id=None: released.append((scope, reservation_id)) or 1,
        )
        monkeypatch.setattr(
            "venue_copilot.detached.deregister_live_session",
            lambda sid: deregistered.append(sid) or True,
        )

        rc, payload = detached.stop_detached(
            adapter,
            self._plan(),
            session_row={"session_id": "sid-42", "venue": {"target": "venue"}},
        )

        assert rc == 0
        assert payload["stopped"] is True
        assert payload["deregistered"] == "sid-42"
        assert adapter.keeper_stopped is True
        assert released == [("anchor-repo@venue", None)]
        assert deregistered == ["sid-42"]

    def test_stop_detached_failure_releases_nothing(self, monkeypatch) -> None:
        from venue_copilot import detached

        adapter = _Adapter()
        adapter.run = lambda command, *, timeout: (3, "STILL_RUNNING\n", "")
        released = []
        monkeypatch.setattr(
            "venue_copilot.detached.release_cli_mode",
            lambda *a, **k: released.append(a) or 1,
        )

        rc, payload = detached.stop_detached(adapter, self._plan(), session_row={})

        assert rc == 1
        assert "could not verify" in payload["error"]
        assert released == []


def _bash() -> str | None:
    import shutil
    import sys

    if sys.platform != "win32":
        return shutil.which("bash")
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramW6432", "")):
        candidate = Path(base) / "Git" / "bin" / "bash.exe"
        if base and candidate.is_file():
            return str(candidate)
    return None


@pytest.mark.skipif(_bash() is None, reason="needs bash")
def test_registration_credentials_write_a_complete_forwarded_route(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    script = (
        'flock() { touch "$1"; shift; "$@"; }; '
        + registration_credentials_script("tok-1", 62254)
    )
    subprocess.run([_bash(), "-c", script], env=env, check=True)
    route = json.loads((tmp_path / ".agent-bridge" / "active.json").read_text())
    # A "bind" lets the venue's agent-bridge CLI parse the route (without it,
    # it fell back to its default port and started a local daemon over it).
    assert route == {"active": {"bind": "127.0.0.1", "port": 62254, "forwarded": True}}
    assert (tmp_path / ".agent-bridge" / "auth.yaml").read_text() == "token: tok-1\n"
    assert not list((tmp_path / ".agent-bridge").glob("*.XXXXXX"))
    assert (tmp_path / ".agent-bridge" / "active.lock").exists()


def _bash_supports_python_fcntl() -> bool:
    bash = _bash()
    if bash is None:
        return False
    return (
        subprocess.run(
            [
                bash,
                "-c",
                "py=$(command -v python3 || command -v python || true); "
                'test -n "$py" && "$py" -c "import fcntl"',
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


@pytest.mark.skipif(not _bash_supports_python_fcntl(), reason="needs bash + python fcntl")
def test_registration_credentials_fallback_locks_with_python_fcntl(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    script = (
        "command() { "
        "if [ \"$1\" = -v ] && [ \"$2\" = flock ]; then return 1; fi; "
        "builtin command \"$@\"; "
        "}; "
        + registration_credentials_script("tok-2", 62255)
    )
    subprocess.run([_bash(), "-c", script], env=env, check=True)
    bridge = tmp_path / ".agent-bridge"
    assert json.loads((bridge / "active.json").read_text()) == {
        "active": {"bind": "127.0.0.1", "port": 62255, "forwarded": True}
    }
    assert (bridge / "auth.yaml").read_text() == "token: tok-2\n"
    assert (bridge / "active.lock").exists()


@pytest.mark.skipif(_bash() is None, reason="needs bash")
def test_registration_credentials_fails_without_locking_tool(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    script = (
        "command() { "
        "if [ \"$1\" = -v ] && { [ \"$2\" = flock ] || [ \"$2\" = python3 ] || [ \"$2\" = python ]; }; "
        "then return 1; fi; "
        "builtin command \"$@\"; "
        "}; "
        + registration_credentials_script("tok-3", 62256)
    )
    result = subprocess.run([_bash(), "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "cannot lock active.json" in result.stderr
    assert not (tmp_path / ".agent-bridge" / "active.json").exists()
