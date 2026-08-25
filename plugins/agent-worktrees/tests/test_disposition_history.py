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


# --- session-tag + kind + recovery digest (Phase 2) -----------------------

def test_append_records_kind_and_session(_tracking_dir):
    dh.append("wt-k", at="t1", summary="s", title=None, follow_up=False,
              changed=["summary"], kind="status", session_id="sess-aaa")
    dh.append("wt-k", at="t2", summary="", title=None, follow_up=False,
              changed=[], kind="bind", session_id="sess-bbb")
    hist = dh.read("wt-k")
    # status is the neutral default -> omitted; bind is explicit -> present
    assert "kind" not in hist[0]
    assert hist[0]["session"] == "sess-aaa"
    assert hist[1]["kind"] == "bind"
    assert hist[1]["session"] == "sess-bbb"


def test_status_only_history_stays_byte_compatible(_tracking_dir):
    # A status write with no session id must produce the legacy line shape
    # (no kind, no session), so old readers and byte-comparisons are unaffected.
    dh.append("wt-l", at="t1", summary="s", title="t", follow_up=True,
              changed=["summary", "title", "follow_up"])
    e = dh.read("wt-l")[0]
    assert set(e.keys()) == {"at", "changed", "title", "summary", "follow_up"}


def test_digest_empty_when_no_history(_tracking_dir):
    assert dh.digest("nope") == ""


def test_digest_renders_recent_entries(_tracking_dir):
    dh.append("wt-m", at="2026-01-01T00:00:01", summary="did A", title="Focus A",
              follow_up=False, changed=["summary", "title"], session_id="s123456")
    dh.append("wt-m", at="2026-01-01T00:00:02", summary="", title=None,
              follow_up=False, changed=[], kind="bind", session_id="b987654")
    out = dh.digest("wt-m")
    assert "recent history" in out
    assert "did A" in out
    assert "[status" in out and "[bind" in out
    # session tail is surfaced
    assert "123456" in out and "987654" in out


def test_digest_honors_limit(_tracking_dir):
    for i in range(10):
        dh.append("wt-n", at=f"t{i}", summary=f"s{i}", title=None,
                  follow_up=False, changed=["summary"])
    out = dh.digest("wt-n", limit=3)
    assert "s9" in out and "s8" in out and "s7" in out
    assert "s6" not in out


def test_digest_bounds_rendering_without_mutating_recent_history(_tracking_dir):
    summaries = [f"summary-{i}-" + ("x" * 500) for i in range(10)]
    for i, summary in enumerate(summaries):
        dh.append("wt-bounded", at=f"t{i}", summary=summary, title=None,
                  follow_up=False, changed=["summary"])

    out = dh.digest("wt-bounded", limit=8)

    assert len(out) <= dh.DIGEST_MAX_CHARS
    assert "summary-9-" in out
    assert "summary-0-" not in out
    assert "..." in out
    assert dh.read("wt-bounded")[-1]["summary"] == summaries[-1]


def test_set_disposition_tags_session(_tracking_dir):
    from agent_worktrees.tracking import WorktreeRecord, set_disposition

    rec = WorktreeRecord(
        worktree_id="wt-p", branch="b", worktree_path="/w", repo="r",
        machine="m", platform="wsl", started_at="t", last_resumed_at="t",
        resume_count=0, title=None, status="active", completed_at=None,
        sessions=[],
    )
    set_disposition(rec, summary="hello", session_id="sess-xyz", save=False)
    e = dh.read("wt-p")[0]
    assert e["summary"] == "hello"
    assert e["session"] == "sess-xyz"
