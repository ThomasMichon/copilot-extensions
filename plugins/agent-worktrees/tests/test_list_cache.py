"""Tests for the coalescing list result-cache (list_cache.py, cx#918)."""

from __future__ import annotations

import json
import types

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import list_cache as lc


@pytest.fixture
def _cache_home(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path)
    return tmp_path


def _args(**kw):
    base = dict(classify=True, mux_details=True, all=False,
                include_other_platforms=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


# --- key ---------------------------------------------------------------------

def test_cache_key_stable_and_axis_sensitive():
    a = _args()
    assert lc.cache_key(a, project="p", tracking_status="all") == \
        lc.cache_key(a, project="p", tracking_status="all")
    # each output-affecting axis changes the key
    assert lc.cache_key(a, project="p", tracking_status="all") != \
        lc.cache_key(_args(classify=False), project="p", tracking_status="all")
    assert lc.cache_key(a, project="p", tracking_status="all") != \
        lc.cache_key(a, project="OTHER", tracking_status="all")
    assert lc.cache_key(a, project="p", tracking_status="all") != \
        lc.cache_key(a, project="p", tracking_status="active")


# --- ttl resolution ----------------------------------------------------------

@pytest.mark.parametrize("val,expected", [
    ("", lc._DEFAULT_TTL), ("6", 6.0), ("2.5", 2.5),
    ("0", 0.0), ("off", 0.0), ("false", 0.0), ("no", 0.0),
    ("-3", 0.0), ("garbage", lc._DEFAULT_TTL),
])
def test_ttl_seconds(val, expected):
    assert lc.ttl_seconds(env={lc._TTL_ENV: val}) == expected


# --- read/write roundtrip ----------------------------------------------------

def test_write_then_fresh_read(_cache_home):
    k = "abc123"
    lc.write(k, {"worktrees": [{"id": "x"}]}, now=1000.0)
    assert lc.read_fresh(k, ttl=4, now=1002.0) == {"worktrees": [{"id": "x"}]}


def test_resident_lease_extends_only_warmed_entry(_cache_home):
    lc.write(
        "warm", {"worktrees": [{"id": "x"}]},
        now=1000.0, fresh_for=45.0)
    assert lc.read_fresh("warm", ttl=4, now=1044.0) == {
        "worktrees": [{"id": "x"}]}
    assert lc.read_fresh("warm", ttl=4, now=1046.0) is None


def test_read_expired_is_miss(_cache_home):
    k = "abc123"
    lc.write(k, {"worktrees": []}, now=1000.0)
    assert lc.read_fresh(k, ttl=4, now=1010.0) is None


def test_read_missing_is_none(_cache_home):
    assert lc.read_fresh("nope", ttl=4, now=1.0) is None


def test_ttl_zero_disables_read_and_write(_cache_home, monkeypatch):
    monkeypatch.setenv(lc._TTL_ENV, "0")
    lc.write("k", {"worktrees": [1]}, now=1.0)          # no-op when disabled
    assert not (_cache_home / "list-cache" / "k.json").exists()
    # even a present file is ignored when ttl<=0
    monkeypatch.delenv(lc._TTL_ENV, raising=False)
    lc.write("k", {"worktrees": [1]}, now=1.0)
    assert lc.read_fresh("k", ttl=0, now=1.0) is None


def test_write_is_atomic_json(_cache_home):
    lc.write("k", {"worktrees": [{"id": "x"}]}, now=5.0)
    p = _cache_home / "list-cache" / "k.json"
    env = json.loads(p.read_text("utf-8"))
    assert env["stamped_at"] == 5.0 and env["payload"] == {"worktrees": [{"id": "x"}]}
    # no leftover temp files
    assert not list((_cache_home / "list-cache").glob("*.tmp"))


def test_corrupt_cache_is_a_miss_not_a_raise(_cache_home):
    d = _cache_home / "list-cache"
    d.mkdir()
    (d / "k.json").write_text("NOT JSON", encoding="utf-8")
    assert lc.read_fresh("k", ttl=4, now=1.0) is None


def test_non_dict_payload_is_a_miss(_cache_home):
    # A structurally valid envelope whose payload isn't a {worktrees:...} dict
    # (corruption / schema drift) must be treated as a miss, never served -- so
    # _json_output can't crash on a non-dict and the fail-open contract holds.
    d = _cache_home / "list-cache"
    d.mkdir()
    (d / "k.json").write_text(
        json.dumps({"stamped_at": 1000.0, "payload": ["not", "a", "dict"]}),
        encoding="utf-8")
    assert lc.read_fresh("k", ttl=4, now=1001.0) is None
    (d / "m.json").write_text(
        json.dumps({"stamped_at": 1000.0, "payload": {"no": "worktrees key"}}),
        encoding="utf-8")
    assert lc.read_fresh("m", ttl=4, now=1001.0) is None


# --- integration: cmd_list coalesces repeated calls --------------------------

def test_cmd_list_json_coalesces_scans(_cache_home, monkeypatch):
    """A second `list --json` within the TTL reuses the cache and skips the
    expensive scan; `--fresh` forces a re-scan. This is the CPU-churn fix."""
    from agent_worktrees import __main__ as m
    from agent_worktrees import sessions, tracking

    rec = tracking.WorktreeRecord(
        worktree_id="wt-x", branch="b", worktree_path="/w/x", repo="r",
        machine="m", platform="windows", started_at="", last_resumed_at="",
        resume_count=0, title="t", status="active", completed_at=None)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: _cache_home / "trk")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(cfg, "project_name", lambda: "testproj")
    monkeypatch.setattr(tracking, "list_records", lambda *a, **k: [rec])
    monkeypatch.setattr(m, "_worktree_to_dict",
                        lambda rec, **k: {"worktree_id": rec.worktree_id, "title": "t"})

    scans = {"n": 0}

    def _scan(records):
        scans["n"] += 1
        return sessions.SessionContext()
    monkeypatch.setattr(sessions, "scan_sessions_fast", _scan)

    outputs = []
    monkeypatch.setattr(m, "_json_output", lambda payload: outputs.append(payload))

    def _ns(fresh=False):
        return types.SimpleNamespace(
            json=True, stream=False, cache_only=False, mux_details=False,
            classify=False, all=True, tracking_status="all",
            include_other_platforms=False, fresh=fresh)

    monkeypatch.setenv(lc._TTL_ENV, "30")  # generous TTL so the 2nd call hits
    m.cmd_list(_ns())            # scan #1 -> writes cache
    m.cmd_list(_ns())            # cache hit -> NO scan
    assert scans["n"] == 1
    assert outputs[0] == outputs[1]  # identical payload
    m.cmd_list(_ns(fresh=True))  # --fresh -> re-scan
    assert scans["n"] == 2


def test_filter_list_worktree_accepts_exact_and_unique_suffix():
    from agent_worktrees import __main__ as m

    records = [
        types.SimpleNamespace(worktree_id="project-win-20260829-ab12"),
        types.SimpleNamespace(worktree_id="project-win-20260829-cd34"),
    ]

    assert m._filter_list_worktree(
        records, "project-win-20260829-ab12") == [records[0]]
    assert m._filter_list_worktree(records, "cd34") == [records[1]]
    assert m._filter_list_worktree(records, "none") == []


def test_filter_list_worktree_rejects_ambiguous_suffix():
    from agent_worktrees import __main__ as m

    records = [
        types.SimpleNamespace(worktree_id="project-win-a-same"),
        types.SimpleNamespace(worktree_id="project-win-b-same"),
    ]

    with pytest.raises(ValueError, match="Ambiguous short ID"):
        m._filter_list_worktree(records, "same")


def test_list_payload_includes_bridge_live_signal(monkeypatch):
    from agent_worktrees import __main__ as m
    from agent_worktrees import reclaim, sessions, tracking

    rec = tracking.WorktreeRecord(
        worktree_id="wt-x", branch="b", worktree_path="/w/x", repo="r",
        machine="m", platform="windows", started_at="", last_resumed_at="",
        resume_count=0, title="t", status="active", completed_at=None)
    monkeypatch.setattr(sessions, "mux_status_many", lambda ids: {})
    monkeypatch.setattr(
        sessions, "scan_sessions_fast", lambda records: sessions.SessionContext())
    monkeypatch.setattr(reclaim, "bare_orphan_worktree_ids", lambda: set())
    monkeypatch.setattr(reclaim, "live_bridge_worktrees", lambda: {"wt-x"})
    seen = {}

    def serialize(record, **kwargs):
        seen.update(kwargs)
        return {"id": record.worktree_id, "title": record.title}

    monkeypatch.setattr(m, "_worktree_to_dict", serialize)
    args = types.SimpleNamespace(mux_details=True, classify=False)

    assert m._build_list_json_payload(
        args, [rec], stamp_session_state=False)["worktrees"] == [
            {"id": "wt-x", "title": "t"}]
    assert seen["bridge_live_wts"] == {"wt-x"}


def test_resident_warm_populates_exact_demanded_shapes(_cache_home, monkeypatch):
    """The daemon refreshes Windows Picker and bridge shapes without guessing."""
    from agent_worktrees import __main__ as m

    monkeypatch.setattr(cfg, "project_name", lambda: "testproj")
    monkeypatch.setattr(m, "_list_records_for_args", lambda args: ["record"])
    monkeypatch.setattr(
        m, "_build_list_json_payload",
        lambda args, records, **kw: {"worktrees": [{"id": records[0]}]})
    picker_args = _args(include_other_platforms=True)
    bridge_args = _args(classify=False)
    for args in (picker_args, bridge_args):
        key = lc.cache_key(args, project="testproj", tracking_status="all")
        lc.note_demand(
            key, args, project="testproj", tracking_status="all", now=1000.0)

    monkeypatch.setattr(lc.time, "time", lambda: 1001.0)
    assert m._warm_list_cache_for_active_project(interval=15) == 2
    for args in (picker_args, bridge_args):
        key = lc.cache_key(args, project="testproj", tracking_status="all")
        assert lc.read_fresh(key, now=1040.0) == {
            "worktrees": [{"id": "record"}]}


def test_resident_warm_respects_disabled_cache(_cache_home, monkeypatch):
    from agent_worktrees import __main__ as m

    monkeypatch.setenv(lc._TTL_ENV, "0")
    monkeypatch.setattr(m, "_list_records_for_args",
                        lambda args: pytest.fail("disabled cache must not scan"))
    assert m._warm_list_cache_for_active_project() == 0


def test_recent_demands_are_project_scoped_and_expire(_cache_home):
    a = _args()
    lc.note_demand(
        "a", a, project="p1", tracking_status="all", now=1000.0)
    lc.note_demand(
        "b", a, project="p2", tracking_status="active", now=1000.0)

    assert [d["key"] for d in lc.recent_demands("p1", now=1100.0)] == ["a"]
    assert lc.recent_demands("p1", now=1201.0) == []
    assert not (_cache_home / "list-cache" / "demand" / "a.json").exists()


def test_recent_demand_projects_reports_all_active_projects(_cache_home):
    args = _args()
    lc.note_demand(
        "a", args, project="p1", tracking_status="all")
    lc.note_demand(
        "b", args, project="p2", tracking_status="all")

    assert lc.recent_demand_projects() == {"p1", "p2"}
