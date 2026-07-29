"""Render coverage for the worktree/session ``doctor`` bare-orphan surface.

``cmd_doctor`` surfaces machine-wide **bare** (un-muxed) bound Copilots -- ones
invisible to the ``wt-<id>`` mux fleet view -- report-only, pointing at the
``reclaim`` verb. Here we exercise the render of that section directly (the
resolution logic itself is covered in ``test_reclaim.py``).
"""

from __future__ import annotations

from agent_worktrees import __main__ as m


def _report(**over) -> dict:
    base = {
        "project": "p",
        "mode": "report",
        "yaml_integrity": {"bad": 0, "repairable": 0, "repaired": 0, "files": []},
        "backfill": {"registry": 0, "titles": 0, "sessions": 0, "worktrees": 0},
        "stale_status": {"found": 0, "fixed": 0, "ids": []},
        "empty_sessions": {"count": 0, "removed_dirs": 0, "removed_rows": 0},
        "misaligned": {"count": 0, "worktrees": []},
        "bare_orphans": {"count": 0, "items": []},
    }
    base.update(over)
    return base


def test_render_lists_bare_orphans(capfd):
    rep = _report(bare_orphans={"count": 1, "items": [
        {"session_id": "abcdef123456", "pid": 22068,
         "worktree_id": "wtA", "cwd": "/w/a"}]})
    m._render_doctor_report(rep, applied=False, gc_applied=False)
    out = capfd.readouterr().out
    assert "Bare (un-muxed) Copilot orphan(s): 1" in out
    assert "abcdef12" in out and "22068" in out
    assert "reclaim --worktree-id" in out


def test_render_clean_when_no_orphans(capfd):
    m._render_doctor_report(_report(), applied=False, gc_applied=False)
    assert "No bare (un-muxed) Copilot orphans" in capfd.readouterr().out


def test_render_tolerates_missing_key(capfd):
    """A report predating the bare-orphan field renders without KeyError."""
    rep = _report()
    del rep["bare_orphans"]
    m._render_doctor_report(rep, applied=False, gc_applied=False)
    assert "No bare (un-muxed) Copilot orphans" in capfd.readouterr().out
