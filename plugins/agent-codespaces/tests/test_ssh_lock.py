"""Tests for per-target SSH serialization in the ssh CLI (#20)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

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
