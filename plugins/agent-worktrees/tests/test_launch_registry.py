"""Tests for :mod:`agent_worktrees.launch_registry` (#4454 follow-up).

A live worktree launcher registers its own root pid here so a version-cutover
reap (:mod:`stale_runtime_reap`) recognizes its short-lived subprocess calls as
protected work rather than a wedged orphan on a superseded runtime slot.
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys

from agent_worktrees import launch_registry


def test_register_launch_writes_a_lock_file_for_this_process(tmp_path):
    ok = launch_registry.register_launch(tmp_path, "wt-abc", pid=os.getpid())
    assert ok
    lock_path = tmp_path / "launch-locks" / f"launch.wt-abc.{os.getpid()}.lock"
    assert lock_path.exists()


def test_register_launch_rejects_empty_worktree_id(tmp_path):
    assert launch_registry.register_launch(tmp_path, "", pid=os.getpid()) is False


def test_register_launch_defaults_pid_to_this_process(tmp_path):
    ok = launch_registry.register_launch(tmp_path, "wt-default")
    assert ok
    lock_path = tmp_path / "launch-locks" / f"launch.wt-default.{os.getpid()}.lock"
    assert lock_path.exists()
    assert os.getpid() in launch_registry.active_launch_pids(tmp_path)


def test_register_launch_rejects_falsy_nonnone_pid(tmp_path):
    assert launch_registry.register_launch(tmp_path, "wt-abc", pid=0) is False


def test_active_launch_pids_reports_live_registered_pid(tmp_path):
    launch_registry.register_launch(tmp_path, "wt-live", pid=os.getpid())
    assert os.getpid() in launch_registry.active_launch_pids(tmp_path)


def test_active_launch_pids_prunes_a_dead_pid_and_excludes_it(tmp_path):
    # Register while genuinely alive -- real usage never registers a dead
    # pid, and doing so here matters: a lock written for an already-dead pid
    # has no start-time token to defeat pid-reuse, which is a real hazard on
    # a busy machine that recycles pids within milliseconds.
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    proc_pid = proc.pid
    try:
        launch_registry.register_launch(tmp_path, "wt-dead", pid=proc_pid)
        lock_path = tmp_path / "launch-locks" / f"launch.wt-dead.{proc_pid}.lock"
        assert lock_path.exists()

        proc.kill()
        proc.wait()
        # Windows keeps a process object queryable (OpenProcess still
        # succeeds) while THIS test still holds its own Popen handle open --
        # an artifact of the test harness, never true for the real feature
        # (the registering launcher and the reaper are unrelated processes
        # with no such handle). Drop it so liveness reflects reality.
        del proc
        gc.collect()

        pids = launch_registry.active_launch_pids(tmp_path)

        assert proc_pid not in pids
        assert not lock_path.exists()  # self-healed: the stale entry is pruned
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        except NameError:
            pass


def test_active_launch_pids_empty_when_directory_absent(tmp_path):
    assert launch_registry.active_launch_pids(tmp_path / "does-not-exist") == set()


def test_register_launch_keys_by_worktree_and_pid_so_concurrent_launchers_coexist(tmp_path):
    """Two live launcher roots for the SAME worktree (a fast re-attach racing
    an already-running one, #4481 review) must both stay protected -- keying
    by worktree id alone would let the second registration evict the first."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    try:
        launch_registry.register_launch(tmp_path, "wt-concurrent", pid=proc.pid)
        launch_registry.register_launch(tmp_path, "wt-concurrent", pid=os.getpid())

        pids = launch_registry.active_launch_pids(tmp_path)

        assert proc.pid in pids
        assert os.getpid() in pids
    finally:
        proc.kill()
        proc.wait()


def test_register_launch_is_idempotent_for_the_same_worktree_and_pid(tmp_path):
    launch_registry.register_launch(tmp_path, "wt-same", pid=os.getpid())
    launch_registry.register_launch(tmp_path, "wt-same", pid=os.getpid())

    lock_dir = tmp_path / "launch-locks"
    assert len(list(lock_dir.glob("launch.*.lock"))) == 1
    assert os.getpid() in launch_registry.active_launch_pids(tmp_path)


class _Args:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_cmd_register_launch_registers_via_config_install_dir(monkeypatch, tmp_path):
    from agent_worktrees import config as _cfg

    monkeypatch.setattr(_cfg, "install_dir", lambda: tmp_path)

    rc = launch_registry.cmd_register_launch(
        _Args(worktree_id="wt-cli", pid=os.getpid(), launch_id="abc123")
    )

    assert rc == 0
    assert os.getpid() in launch_registry.active_launch_pids(tmp_path)


def test_cmd_register_launch_returns_nonzero_without_worktree_id(tmp_path):
    assert launch_registry.cmd_register_launch(_Args(worktree_id=None, pid=None)) == 1


def test_cmd_register_launch_returns_nonzero_when_the_write_fails(monkeypatch, tmp_path):
    from agent_worktrees import config as _cfg

    monkeypatch.setattr(_cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(launch_registry, "register_launch", lambda *a, **k: False)

    rc = launch_registry.cmd_register_launch(_Args(worktree_id="wt-cli", pid=os.getpid()))

    assert rc == 1


def test_add_parsers_registers_the_register_launch_subcommand():
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    launch_registry.add_parsers(sub)

    args = parser.parse_args(["register-launch", "--worktree-id", "wt-x", "--pid", "123"])

    assert args.command == "register-launch"
    assert args.worktree_id == "wt-x"
    assert args.pid == 123
