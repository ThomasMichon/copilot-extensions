"""Launch overlap and retirement exclusion use the actual per-worktree lease."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import launch_admission
from agent_worktrees import cleanup_gc_cli, finalize, worktree_lifecycle


@pytest.mark.parametrize("release", ["exit", "terminate"])
def test_real_launch_owner_expands_target_exclusion_and_retains_legacy_barrier(
    tmp_path, release,
):
    path = tmp_path / "one.yaml"
    other = tmp_path / "two.yaml"
    code = (
        "import sys; from pathlib import Path; from types import SimpleNamespace; "
        "from agent_worktrees import agent_policy, bridge_acquisition_launch; "
        "agent_policy.require_record = lambda _: None\n"
        "with bridge_acquisition_launch.launch_fence("
        "SimpleNamespace(worktree_root=sys.argv[2]), Path(sys.argv[1])):\n"
        " print('ready', flush=True)\n"
        " sys.stdin.readline()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(path), str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **no_window_kwargs(),
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(TimeoutError), worktree_lifecycle.guard(path, timeout=0):
            pytest.fail("A launch owner must exclude removal of its target")
        with worktree_lifecycle.guard(other, timeout=0):
            assert child.poll() is None
        # Older retirement owners still require this compatibility barrier.
        with pytest.raises(TimeoutError), finalize.FinalizeLock(
            tmp_path / ".finalize.lock", timeout=0.01, stale_after=3600,
        ):
            pass
        if release == "exit":
            child.communicate("\n", timeout=10)
            assert child.returncode == 0
        else:
            child.terminate()
            child.communicate(timeout=10)
        with worktree_lifecycle.guard(path, timeout=0):
            pass
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def test_lifecycle_and_seed_admission_are_distinct(tmp_path):
    path = tmp_path / "one.yaml"
    with worktree_lifecycle.guard(path), launch_admission.guard(path):
        with pytest.raises(TimeoutError), worktree_lifecycle.guard(path, timeout=0):
            pass
    with worktree_lifecycle.guard(path, timeout=0):
        with launch_admission.guard(path):
            pass


@pytest.mark.parametrize("force", [False, True])
def test_cleanup_refuses_busy_launch_before_record_or_liveness_read(
    tmp_path, monkeypatch, force,
):
    path = tmp_path / "one.yaml"
    path.write_text("unchanged", encoding="utf-8")
    read = Mock(side_effect=AssertionError("busy launch must precede record read"))
    monkeypatch.setattr(cleanup_gc_cli.tracking, "load_record", read)
    real_guard = worktree_lifecycle.guard
    monkeypatch.setattr(worktree_lifecycle, "guard", lambda p: real_guard(p, timeout=0))
    with real_guard(path):
        result = cleanup_gc_cli._revalidate_cleanup_safety(
            "one", repo=SimpleNamespace(), tracking_path=tmp_path, force=force,
            reap=Mock(side_effect=AssertionError("busy launch must not reap")),
        )
    assert not result.cleanable and result.bucket == "locked"
    read.assert_not_called()
    assert path.read_text(encoding="utf-8") == "unchanged"
