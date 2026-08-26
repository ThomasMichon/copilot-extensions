"""Tests for per-target SSH serialization in the ssh CLI (#20)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_codespaces import __main__ as cli
from agent_codespaces.__main__ import _BUSY_EXIT, main
from agent_codespaces.resolver import _build_spawn_command


class TestSpawnCommandForce:
    def test_bridge_spawn_uses_force(self):
        cmd = _build_spawn_command("cs-alpha", "copilot --acp --stdio")
        assert "--force" in cmd
        # --force must precede the --remote-cmd-file payload so it is parsed as
        # a flag, not swallowed into the remote command reference.
        assert cmd.index("--force") < cmd.index("--remote-cmd-file")
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
        self.port_forwards: list[list[str]] = []

    async def ensure_connected(self, *_args, **_kwargs):
        self.calls.append("ensure_connected")
        if len(_args) >= 3:
            self.port_forwards.append(list(_args[2]))
        if self.hang_connect:
            import asyncio

            await asyncio.sleep(30)
        return SimpleNamespace(config=SimpleNamespace(ssh_target="cs-one"))

    async def exec_command(self, *_args, **_kwargs):
        self.calls.append("exec_command")
        return _FakeCommandResult()

    async def open_stdio_channel(self, *_args, **_kwargs):
        self.calls.append("open_stdio_channel")
        return SimpleNamespace(returncode=0)

    async def disconnect(self, *_args, **_kwargs):
        self.calls.append("disconnect")


class _FailingExecManager:
    async def exec_command(self, *_args, **_kwargs):
        raise RuntimeError("network down")


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

    class _FakeRelayForward:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: locks)
    monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
    monkeypatch.setattr("ssh_manager.SupervisedRelayForward", _FakeRelayForward)
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
        monkeypatch.setattr(cli, "_warm_remote_auth_cache", lambda *_a, **_kw: record("warm"))

        rc = main(["ssh", "cs-diag", "--remote-cmd", "echo ok", "--timeout", "5"])

        assert rc == 0
        assert "relay" in calls
        assert "auth" in calls
        assert "exec_command" in calls
        assert manager.port_forwards == [[]]
        assert "dotfiles" not in calls
        assert "harness" not in calls
        assert "register" not in calls
        assert "hooks" not in calls
        assert "stage" not in calls
        assert "warm" not in calls
        err = capsys.readouterr().err
        assert "[stage 3/ssh-to-target] started" in err
        assert "heavy provisioning skipped" in err
        assert "[stage 7/launch-acp] started: remote command" in err

    def test_auth_cache_warmup_runs_for_stdio_dispatch(
        self, tmp_path, monkeypatch
    ):
        calls: list[str] = []
        manager = _FakeManager(calls)
        _patch_ssh_dependencies(monkeypatch, tmp_path, manager)

        async def record(name):
            calls.append(name)

        async def empty_list(*_a, **_kw):
            return []

        monkeypatch.setattr(cli, "_provision_relay_helpers", lambda *_a: record("relay"))
        monkeypatch.setattr(cli, "_verify_remote_auth", lambda *_a: record("auth"))
        monkeypatch.setattr(cli, "_provision_dotfiles", lambda *_a: record("dotfiles"))
        monkeypatch.setattr(cli, "_provision_harness", lambda *_a: record("harness"))
        monkeypatch.setattr(cli, "_register_codespace_plugins", empty_list)
        monkeypatch.setattr(cli, "_provision_repo_hooks", lambda *_a: record("hooks"))
        monkeypatch.setattr(cli, "_stage_plugins", empty_list)
        monkeypatch.setattr(cli, "_warm_remote_auth_cache", lambda *_a, **_kw: record("warm"))
        monkeypatch.setattr(cli, "_pipe_stdio", lambda *_a: record("pipe"))

        rc = main([
            "ssh",
            "cs-dispatch",
            "--stdio",
            "--remote-cmd",
            "copilot --acp --stdio",
        ])

        assert rc == 0
        assert "warm" in calls
        assert calls.index("auth") < calls.index("warm") < calls.index("open_stdio_channel")


class TestAuthCacheWarmup:
    @pytest.mark.asyncio
    async def test_warmup_is_best_effort(self):
        await cli._warm_remote_auth_cache(
            _FailingExecManager(),
            "cs-offline",
            _fake_config(),
            relay_env="export LC_GIT_CREDENTIAL_RELAY=9857;",
        )

    def test_auth_cache_warmup_can_be_requested_for_diagnostic_remote_cmd(
        self, tmp_path, monkeypatch
    ):
        calls: list[str] = []
        manager = _FakeManager(calls)
        _patch_ssh_dependencies(monkeypatch, tmp_path, manager)

        async def record(name):
            calls.append(name)

        monkeypatch.setattr(cli, "_provision_relay_helpers", lambda *_a: record("relay"))
        monkeypatch.setattr(cli, "_verify_remote_auth", lambda *_a: record("auth"))
        monkeypatch.setattr(cli, "_warm_remote_auth_cache", lambda *_a, **_kw: record("warm"))

        rc = main([
            "ssh",
            "cs-diag",
            "--remote-cmd",
            "echo ok",
            "--auth-cache-warmup",
            "--timeout",
            "5",
        ])

        assert rc == 0
        assert "warm" in calls

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


class TestSshClaimEnforcement:
    """The #897 exclusive worktree-keyed claim gates the ssh connect path."""

    def test_live_claim_conflict_bounces(self, monkeypatch, capsys):
        # Opt back into claim enforcement (conftest disables it by default) and
        # mock the lease seam so no real subprocess/host-state I/O happens.
        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
        monkeypatch.setattr(
            "agent_codespaces.__main__.load_merged_config",
            lambda: SimpleNamespace(credentials=SimpleNamespace(relay_port=9857)),
        )
        monkeypatch.setattr(
            "agent_codespaces.lifecycle.account_for_codespace", lambda name: None,
        )
        from agent_codespaces import lease as lease_mod

        monkeypatch.setattr(
            lease_mod, "resolve_owner_worktree",
            lambda explicit=None, session_id=None: "/wt/mine",
        )
        monkeypatch.setattr(
            lease_mod, "active_worktree_ids", lambda: {"/wt/other", "/wt/mine"},
        )

        def _boom(cs, owner, **kw):
            raise lease_mod.ClaimConflict(cs, "/wt/other", "host-x", 4321)

        monkeypatch.setattr(lease_mod, "claim", _boom)

        rc = main(["ssh", "cs-claimed", "--no-relay"])
        assert rc == _BUSY_EXIT
        err = capsys.readouterr().err
        assert "BUSY" in err
        assert "/wt/other" in err  # names the current owner

    def test_claim_acquired_for_resolved_owner(self, monkeypatch):
        # When the CodeSpace is free the claim is acquired for the resolved owner
        # and the connect proceeds (we short-circuit right after via a sentinel).
        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
        monkeypatch.setattr(
            "agent_codespaces.__main__.load_merged_config",
            lambda: SimpleNamespace(credentials=SimpleNamespace(relay_port=9857)),
        )
        monkeypatch.setattr(
            "agent_codespaces.lifecycle.account_for_codespace", lambda name: None,
        )
        from agent_codespaces import lease as lease_mod

        monkeypatch.setattr(
            lease_mod, "resolve_owner_worktree",
            lambda explicit=None, session_id=None: "/wt/mine",
        )
        monkeypatch.setattr(lease_mod, "active_worktree_ids", lambda: {"/wt/mine"})

        seen = {}

        class _Stop(Exception):
            pass

        def _claim(cs, owner, **kw):
            seen["cs"] = cs
            seen["owner"] = owner
            raise _Stop  # short-circuit before the real connect

        monkeypatch.setattr(lease_mod, "claim", _claim)

        with pytest.raises(_Stop):
            main(["ssh", "cs-free", "--no-relay"])
        assert seen == {"cs": "cs-free", "owner": "/wt/mine"}
