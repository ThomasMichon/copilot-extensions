"""Regression tests for the discovery poller's process-group teardown (#4439).

A timed-out ``list --json --mux-details`` must reap the WHOLE child subtree, not
just the direct child, so a per-worktree mux probe grandchild can never orphan
and keep pinning a core (the #4439 accumulation failure mode).
"""

from __future__ import annotations

import os
import signal

import pytest

from agent_bridge.routes import worktrees

# The process-group teardown path (os.killpg/getpgid + signal.SIGKILL) is
# POSIX-only; the source guards on hasattr(os, "killpg") and falls back to a
# direct child kill on Windows. These tests exercise that POSIX path directly
# (and reference signal.SIGKILL, which does not exist on Windows), so they are
# only meaningful where process groups are available.
_posix_groups_only = pytest.mark.skipif(
    not hasattr(os, "getpgid") or not hasattr(signal, "SIGKILL"),
    reason="process groups / SIGKILL unavailable on this platform (Windows)",
)


class _FakeProc:
    def __init__(self, pid: int = 4321, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.killed = False

    def kill(self) -> None:
        self.killed = True


def test_none_and_finished_are_noops() -> None:
    worktrees._kill_process_tree(None)  # must not raise
    worktrees._kill_process_tree(_FakeProc(returncode=0))  # already exited -> no-op


@_posix_groups_only
def test_kills_process_group_when_available(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: calls.append((pgid, sig)), raising=False)

    proc = _FakeProc(pid=4321)
    worktrees._kill_process_tree(proc)

    # The whole group (pgid == pid here) gets SIGKILL; not a mere direct kill().
    assert calls == [(4321, signal.SIGKILL)]
    assert proc.killed is False


def test_falls_back_to_direct_kill_when_group_unavailable(monkeypatch) -> None:
    def _boom(_pid: int) -> int:
        raise OSError("no pgid")

    monkeypatch.setattr(os, "getpgid", _boom, raising=False)
    proc = _FakeProc(pid=4321)
    worktrees._kill_process_tree(proc)
    assert proc.killed is True


@_posix_groups_only
def test_never_raises_on_dead_group(monkeypatch) -> None:
    def _gone(_pid: int) -> int:
        raise ProcessLookupError

    monkeypatch.setattr(os, "getpgid", _gone, raising=False)
    proc = _FakeProc(pid=4321)
    worktrees._kill_process_tree(proc)  # must not raise; group already gone
    assert proc.killed is False
