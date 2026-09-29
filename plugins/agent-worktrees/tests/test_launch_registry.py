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
    lock_path = tmp_path / "launch-locks" / "launch.wt-abc.lock"
    assert lock_path.exists()


def test_register_launch_rejects_empty_worktree_id(tmp_path):
    assert launch_registry.register_launch(tmp_path, "", pid=os.getpid()) is False


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
        lock_path = tmp_path / "launch-locks" / "launch.wt-dead.lock"
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


def test_register_launch_overwrites_a_prior_entry_for_the_same_worktree(tmp_path):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    stale_pid = proc.pid
    proc.wait()
    launch_registry.register_launch(tmp_path, "wt-same", pid=stale_pid)
    launch_registry.register_launch(tmp_path, "wt-same", pid=os.getpid())

    pids = launch_registry.active_launch_pids(tmp_path)

    assert os.getpid() in pids
    assert stale_pid not in pids


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


def test_cmd_register_launch_is_a_no_op_without_worktree_id(tmp_path):
    assert launch_registry.cmd_register_launch(_Args(worktree_id=None, pid=None)) == 0
