"""Tests for session_host.osutil tree-kill helpers (dotfiles #911).

The POSIX branch of ``kill_pid`` must tree-kill (reap the descendant MCP-bridge
/ sub-agent processes copilot spawned), not just the single pid. These tests
exercise the ``/proc``-walk that discovers those descendants, using a mocked
``/proc`` so they run on any platform.
"""

from __future__ import annotations

import builtins
import io

from agent_bridge.session_host import osutil


def _mock_proc(monkeypatch, tree: dict[int, int]) -> None:
    """Install a fake Linux ``/proc`` where ``tree`` maps pid -> ppid."""
    monkeypatch.setattr(osutil.sys, "platform", "linux")
    monkeypatch.setattr(osutil.os, "listdir", lambda p: [str(x) for x in tree] + ["self"])
    real_open = builtins.open

    def fake_open(path, *a, **k):
        if isinstance(path, str) and path.startswith("/proc/"):
            pid = int(path.split("/")[2])
            ppid = tree[pid]
            # comm may contain spaces AND parens -- exercise the rfind(')') parse.
            return io.BytesIO(f"{pid} (we (ir)d comm) S {ppid} 0 0".encode())
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)


def test_posix_descendants_walks_full_tree(monkeypatch):
    # 100 is root; 200,201 are its children; 300 is under 200; 999 is unrelated.
    _mock_proc(monkeypatch, {100: 1, 200: 100, 201: 100, 300: 200, 999: 1})
    assert sorted(osutil._posix_descendant_pids(100)) == [200, 201, 300]


def test_posix_descendants_leaf_is_empty(monkeypatch):
    _mock_proc(monkeypatch, {100: 1, 200: 100})
    assert osutil._posix_descendant_pids(200) == []


def test_posix_descendants_non_linux(monkeypatch):
    monkeypatch.setattr(osutil.sys, "platform", "win32")
    assert osutil._posix_descendant_pids(123) == []


def test_kill_pid_none_is_noop():
    # Must not raise on a missing pid.
    osutil.kill_pid(None)
    osutil.kill_pid(0)
