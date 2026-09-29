"""Tests for :mod:`agent_worktrees.stale_runtime_reap`.

#4268: a one-shot CLI verb invocation mid-flight on a superseded runtime slot
has no self-check of its own (only the resident status-monitor loop rechecks
``_runtime_superseded`` each tick), so it can wedge and pile up across every
deploy it survives unless the cutover reap also sweeps for it, not just the
status-monitor singleton's own known lock pid. See test_status_monitor.py for
the ``_restart_status_monitor``/``cmd_status_monitor_restart`` wiring tests.
"""

from __future__ import annotations

import os

from agent_worktrees import stale_runtime_reap


class _FakeCfg:
    def __init__(self, install_dir):
        self._install_dir = install_dir

    def install_dir(self):
        return self._install_dir


def test_reap_sweeps_versions_root_excluding_current_slot(monkeypatch, tmp_path):
    install_root = tmp_path / ".agent-worktrees"
    monkeypatch.setattr(
        stale_runtime_reap.sys, "prefix", str(install_root / "versions" / "2.0.0")
    )
    seen = {}

    def _fake_terminate(root, *, exclude=None, exclude_pids=None, protect_ancestors=None):
        seen["root"] = root
        seen["exclude"] = exclude
        seen["protect_ancestors"] = protect_ancestors
        return [
            {"pid": 111, "name": "python.exe", "executable": "x", "killed": True},
            {"pid": 222, "name": "python.exe", "executable": "y", "killed": False},
        ]

    import agent_worktrees.launch_registry as _launch_registry
    import agent_worktrees.procs as _procs

    monkeypatch.setattr(_procs, "terminate_processes_under_executable", _fake_terminate)
    monkeypatch.setattr(_launch_registry, "active_launch_pids", lambda install_dir: {999})

    killed = stale_runtime_reap.reap(_FakeCfg(install_root))

    assert killed == [111]  # only the actually-killed pid is reported
    assert seen["root"] == str(install_root / "versions")
    assert seen["exclude"] == os.path.realpath(str(install_root / "versions" / "2.0.0"))
    assert seen["protect_ancestors"] == {999}  # registered live launcher roots are forwarded


def test_reap_degrades_to_empty_on_error(monkeypatch, tmp_path):
    import agent_worktrees.procs as _procs

    def _boom(*a, **k):
        raise OSError("enumeration unavailable")

    monkeypatch.setattr(_procs, "terminate_processes_under_executable", _boom)

    assert stale_runtime_reap.reap(_FakeCfg(tmp_path)) == []


def test_summary_bits_empty_when_nothing_reaped():
    assert stale_runtime_reap.summary_bits([]) == []
    assert stale_runtime_reap.summary_bits(None) == []


def test_summary_bits_reports_count():
    assert stale_runtime_reap.summary_bits([111, 222]) == [
        "reaped 2 stale-runtime process(es)"
    ]


def test_summary_suffix_empty_when_nothing_reaped():
    assert stale_runtime_reap.summary_suffix([]) == ""


def test_summary_suffix_prefixes_with_semicolon():
    assert stale_runtime_reap.summary_suffix([111]) == "; reaped 1 stale-runtime process(es)"
