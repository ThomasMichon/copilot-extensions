"""Tests for per-target SSH serialization in the ssh CLI (#20)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

from agent_codespaces import __main__ as cli
from agent_codespaces.__main__ import _BUSY_EXIT, main
from agent_codespaces.resolver import _build_spawn_command


class TestSpawnCommandForce:
    def test_bridge_spawn_uses_force(self):
        cmd = _build_spawn_command("cs-alpha", "copilot --acp --stdio")
        assert "--force" in cmd
        # --force must precede the --remote-cmd payload so it is parsed as a
        # flag, not swallowed into the remote command string.
        assert cmd.index("--force") < cmd.index("--remote-cmd")
        assert "--stdio" in cmd


def _spawn_sleeper() -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)
    return proc


class TestSshBusyRejection:
    def test_busy_target_rejected(self, tmp_path, monkeypatch, capsys):
        locks = tmp_path / "locks"
        locks.mkdir(parents=True)
        monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
        monkeypatch.setattr(
            "agent_codespaces.__main__.load_merged_config",
            lambda: SimpleNamespace(
                credentials=SimpleNamespace(relay_port=9857)
            ),
        )

        from ssh_manager.locks import LockHolder, TargetLock

        # Pre-write a lock held by a *different* live process so acquire is not
        # treated as re-entrant for this test process.
        sleeper = _spawn_sleeper()
        try:
            pre = TargetLock("cs-busy", directory=locks)
            holder = LockHolder(
                pid=sleeper.pid, op="stdio", target="cs-busy",
                started_at=time.time(),
            )
            pre.path.write_text(json.dumps(holder.__dict__), encoding="utf-8")

            rc = main(["ssh", "cs-busy", "--no-relay"])
            assert rc == _BUSY_EXIT
            err = capsys.readouterr().err
            assert "BUSY" in err
            assert str(sleeper.pid) in err
            # The lock file must still belong to the incumbent (not stolen).
            assert pre.read_holder().pid == sleeper.pid
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=5)

    def test_stale_lock_does_not_block(self, tmp_path, monkeypatch):
        """A lock from a dead pid must not wedge the target.

        We can't drive a real SSH here, so assert acquire() reclaims a stale
        lock rather than raising busy.
        """
        locks = tmp_path / "locks"
        locks.mkdir(parents=True)
        from ssh_manager.locks import LockHolder, TargetBusyError, TargetLock

        lock = TargetLock("cs-stale", directory=locks)
        dead = LockHolder(
            pid=2**31 - 1, op="stdio", target="cs-stale", started_at=time.time()
        )
        lock.path.write_text(json.dumps(dead.__dict__), encoding="utf-8")
        try:
            lock.acquire()  # must not raise
            assert lock.read_holder().pid == os.getpid()
        except TargetBusyError:  # pragma: no cover
            raise AssertionError("stale lock should be reclaimed, not busy")
        finally:
            lock.release()


class _FakeCommandResult:
    stdout = "ok"
    stderr = ""
    exit_code = 0
    timed_out = False


class _FakeManager:
    def __init__(self, calls: list[str], *, hang_connect: bool = False) -> None:
        self.calls = calls
        self.hang_connect = hang_connect

    async def ensure_connected(self, *_args, **_kwargs):
        self.calls.append("ensure_connected")
        if self.hang_connect:
            import asyncio

            await asyncio.sleep(30)

    async def exec_command(self, *_args, **_kwargs):
        self.calls.append("exec_command")
        return _FakeCommandResult()

    async def disconnect(self, *_args, **_kwargs):
        self.calls.append("disconnect")


def _fake_config():
    return SimpleNamespace(
        credentials=SimpleNamespace(relay_port=9857),
        dotfiles_repo="example/dotfiles",
        harness_repo="example/harness",
        repos={},
        source_paths=[],
        codespace_plugins=[],
        provision_for_repo=lambda _repo: SimpleNamespace(
            global_on_connect=[],
            global_on_create=[],
            repos={},
        ),
    )


def _patch_ssh_dependencies(monkeypatch, tmp_path, manager):
    locks = tmp_path / "locks"
    locks.mkdir(parents=True)
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    monkeypatch.setattr(
        "agent_codespaces.lifecycle.account_for_codespace",
        lambda _name: None,
    )
    monkeypatch.setattr(cli, "load_merged_config", _fake_config)
    monkeypatch.setattr(cli, "_clear_status_quietly", lambda _name: None)
    monkeypatch.setattr(cli, "_relay_listening", lambda _port: True)
    monkeypatch.setattr("agent_codespaces.relay_token.token_for", lambda _name: "tok")
    return locks


class TestDiagnosticRemoteCmd:
    def test_skips_heavy_provisioning_by_default_and_emits_progress(
        self, tmp_path, monkeypatch, capsys
    ):
        calls: list[str] = []
        manager = _FakeManager(calls)
        _patch_ssh_dependencies(monkeypatch, tmp_path, manager)

        async def record(name):
            calls.append(name)

        monkeypatch.setattr(cli, "_provision_relay_helpers", lambda *_a: record("relay"))
        monkeypatch.setattr(cli, "_verify_remote_auth", lambda *_a: record("auth"))
        monkeypatch.setattr(
            cli, "_provision_dotfiles", lambda *_a: record("dotfiles")
        )
        monkeypatch.setattr(cli, "_provision_harness", lambda *_a: record("harness"))
        monkeypatch.setattr(
            cli, "_register_codespace_plugins", lambda *_a: record("register")
        )
        monkeypatch.setattr(cli, "_provision_repo_hooks", lambda *_a: record("hooks"))
        monkeypatch.setattr(cli, "_stage_plugins", lambda *_a: record("stage"))

        rc = main(["ssh", "cs-diag", "--remote-cmd", "echo ok", "--timeout", "5"])

        assert rc == 0
        assert "relay" in calls
        assert "auth" in calls
        assert "exec_command" in calls
        assert "dotfiles" not in calls
        assert "harness" not in calls
        assert "register" not in calls
        assert "hooks" not in calls
        assert "stage" not in calls
        err = capsys.readouterr().err
        assert "[stage 3/ssh-to-target] started" in err
        assert "heavy provisioning skipped" in err
        assert "[stage 7/launch-acp] started: remote command" in err

    def test_overall_timeout_disconnects_and_releases_lock(
        self, tmp_path, monkeypatch, capsys
    ):
        calls: list[str] = []
        manager = _FakeManager(calls, hang_connect=True)
        locks = _patch_ssh_dependencies(monkeypatch, tmp_path, manager)

        rc = main([
            "ssh",
            "cs-timeout",
            "--no-relay",
            "--remote-cmd",
            "echo ok",
            "--timeout",
            "0.05",
        ])

        assert rc == 124
        assert "disconnect" in calls
        assert not list(locks.glob("*.lock"))
        assert "exceeded 0.05s" in capsys.readouterr().err
