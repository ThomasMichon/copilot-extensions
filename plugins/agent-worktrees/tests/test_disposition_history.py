"""Tests for durable per-worktree disposition history (disposition_history.py)
and its integration with set_disposition + the ``status --history`` read.
"""

from __future__ import annotations

import json

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import disposition_history as dh
from agent_worktrees.tracking import WorktreeRecord, set_disposition


@pytest.fixture
def _tracking_dir(tmp_path, monkeypatch):
    """Point tracking_dir() at a tmp dir so history + records land there."""
    d = tmp_path / "worktrees"
    d.mkdir()
    monkeypatch.setattr(cfg, "tracking_dir", lambda: d)
    return d


def _rec(worktree_id="wt-hist", **kw):
    base = dict(
        worktree_id=worktree_id, branch="b", worktree_path="/tmp/x",
        repo="r", machine="m", platform="wsl",
        started_at="2026-01-01T00:00:00", last_resumed_at="2026-01-01T00:00:00",
        resume_count=0, title="t0", status="active", completed_at=None,
    )
    base.update(kw)
    return WorktreeRecord(**base)


# --- module-level append/read/remove ----------------------------------------

def test_append_and_read_roundtrip(_tracking_dir):
    dh.append("wt-a", at="2026-01-01T00:00:01", summary="s1", title="t1",
              follow_up=True, changed=["summary", "title", "follow_up"])
    dh.append("wt-a", at="2026-01-01T00:00:02", summary="s2", title="t1",
              follow_up=False, changed=["summary", "follow_up"])
    hist = dh.read("wt-a")
    assert [e["summary"] for e in hist] == ["s1", "s2"]  # oldest-first
    assert hist[0]["changed"] == ["summary", "title", "follow_up"]
    assert hist[1]["follow_up"] is False
    # sidecar lives next to the record, as JSONL
    p = _tracking_dir / "wt-a.history.jsonl"
    assert p.is_file()
    assert len(p.read_text("utf-8").splitlines()) == 2
    json.loads(p.read_text("utf-8").splitlines()[0])  # valid JSON per line


def test_read_limit_returns_most_recent(_tracking_dir):
    for i in range(5):
        dh.append("wt-b", at=f"t{i}", summary=f"s{i}", title=None,
                  follow_up=False, changed=["summary"])
    recent = dh.read("wt-b", limit=2)
    assert [e["summary"] for e in recent] == ["s3", "s4"]


def test_append_trims_to_cap(_tracking_dir, monkeypatch):
    monkeypatch.setattr(dh, "MAX_ENTRIES", 3)
    for i in range(6):
        dh.append("wt-c", at=f"t{i}", summary=f"s{i}", title=None,
                  follow_up=False, changed=["summary"])
    hist = dh.read("wt-c")
    assert [e["summary"] for e in hist] == ["s3", "s4", "s5"]  # oldest trimmed


def test_read_missing_is_empty(_tracking_dir):
    assert dh.read("nope") == []


def test_read_skips_malformed_lines(_tracking_dir):
    p = _tracking_dir / "wt-d.history.jsonl"
    p.write_text('{"at":"t0","summary":"ok"}\nNOT JSON\n{"at":"t1","summary":"ok2"}\n',
                 encoding="utf-8")
    hist = dh.read("wt-d")
    assert [e["summary"] for e in hist] == ["ok", "ok2"]


def test_remove_deletes_sidecar(_tracking_dir):
    dh.append("wt-e", at="t", summary="s", title=None, follow_up=False,
              changed=["summary"])
    assert (_tracking_dir / "wt-e.history.jsonl").is_file()
    dh.remove("wt-e")
    assert not (_tracking_dir / "wt-e.history.jsonl").exists()
    dh.remove("wt-e")  # idempotent, no raise


# --- set_disposition integration --------------------------------------------

def test_set_disposition_appends_history(_tracking_dir):
    rec = _rec()
    set_disposition(rec, summary="first pass", follow_up=True)
    set_disposition(rec, title="new focus")
    set_disposition(rec, follow_up=False)
    hist = dh.read(rec.worktree_id)
    assert len(hist) == 3
    assert hist[0]["changed"] == ["summary", "follow_up"]
    assert hist[0]["summary"] == "first pass" and hist[0]["follow_up"] is True
    # each entry is a self-contained snapshot: the title write carries the prior summary
    assert hist[1]["changed"] == ["title"]
    assert hist[1]["title"] == "new focus" and hist[1]["summary"] == "first pass"
    assert hist[2]["changed"] == ["follow_up"] and hist[2]["follow_up"] is False
    # 'at' mirrors the record's status_note_at stamp
    assert hist[2]["at"] == rec.status_note_at


def test_set_disposition_history_entry_matches_record_state(_tracking_dir):
    rec = _rec()
    set_disposition(rec, summary="only summary")
    entry = dh.read(rec.worktree_id)[-1]
    assert entry["summary"] == rec.summary
    assert entry["title"] == rec.title
    assert entry["follow_up"] == rec.follow_up
