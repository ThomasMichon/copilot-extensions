"""Incremental activity-query indexes and public selection semantics."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from agent_worktrees import activity, jsonl_cache

pytestmark = pytest.mark.contract("agent_worktrees.activity.index")


@pytest.fixture(autouse=True)
def clear_reader():
    jsonl_cache.clear()
    yield
    jsonl_cache.clear()


def test_queries_index_only_new_complete_records(tmp_path, monkeypatch):
    path = tmp_path / "activity.jsonl"
    path.write_text("".join(
        json.dumps({"worktree_id": f"wt-{i}", "event": "spawn"}) + "\n"
        for i in range(1000)
    ), encoding="utf-8")
    monkeypatch.setattr(activity, "log_path", lambda: path)
    calls = []
    real_key = jsonl_cache._index_key

    def counted_key(rec, fields):
        calls.append(rec)
        return real_key(rec, fields)

    monkeypatch.setattr(jsonl_cache, "_index_key", counted_key)
    assert activity.read_events(worktree_id="wt-1", event="spawn")[0]["event"] == "spawn"
    assert len(calls) == 1000
    calls.clear()
    for i in range(1000):
        assert len(activity.read_events(worktree_id=f"wt-{i}", event="spawn", limit=64)) == 1
        assert activity.read_events(worktree_id=f"wt-{i}", event="retire", limit=64) == []
    assert calls == [], "different values must not rescan any historical records"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"worktree_id":"wt-1","event":"retire"}\n')
    assert len(activity.read_events(worktree_id="wt-1", event="retire")) == 1
    assert len(calls) == 1, "append indexing must inspect exactly the new record"
    assert len(activity.read_events(worktree_id="wt-1", event="spawn")) == 1
    assert len(calls) == 1


def test_indexed_snapshots_revisit_provisional_tails_and_keep_old_views(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"event":"a","n":1}\n{"event":"a","n":2}', encoding="utf-8")
    first = jsonl_cache.read_jsonl(path, match={"event": "a"})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('\n{"event":"b","n":3}\n{"event":"a","n":')
    second = jsonl_cache.read_jsonl(path, match={"event": "a"})
    assert [r["n"] for r in first] == [1, 2]
    assert [r["n"] for r in second] == [1, 2]
    with path.open("a", encoding="utf-8") as handle:
        handle.write('4}\n')
    assert [r["n"] for r in jsonl_cache.read_jsonl(path, match={"event": "a"})] == [1, 2, 4]
    assert [r["n"] for r in first] == [1, 2]
    assert [r["n"] for r in second] == [1, 2]


@pytest.mark.parametrize("operation", ["replace", "truncate", "invalidate", "delete"])
def test_indexes_follow_reader_invalidation(tmp_path, operation):
    path = tmp_path / "events.jsonl"
    path.write_text('{"event":"old","padding":"long old value"}\n', encoding="utf-8")
    assert len(jsonl_cache.read_jsonl(path, match={"event": "old"})) == 1
    if operation == "replace":
        replacement = tmp_path / "replacement"
        replacement.write_text('{"event":"new"}\n', encoding="utf-8")
        replacement.replace(path)
    elif operation == "truncate":
        path.write_text('{"event":"new"}\n', encoding="utf-8")
    elif operation == "invalidate":
        jsonl_cache.invalidate(path)
        path.write_text('{"event":"new"}\n', encoding="utf-8")
    else:
        path.unlink()
    assert list(jsonl_cache.read_jsonl(path, match={"event": "old"})) == []
    assert len(jsonl_cache.read_jsonl(path, match={"event": "new"})) == (operation != "delete")


def test_all_filters_since_limits_and_result_isolation(tmp_path, monkeypatch):
    path = tmp_path / "activity.jsonl"
    records = [
        {"worktree_id": "wt", "launch_id": "launch", "event": "a",
         "ts": "2020-01-01T00:00:00Z", "data": [1]},
        {"worktree_id": "wt", "launch_id": "launch", "event": "a", "data": [2]},
        {"worktree_id": "wt", "launch_id": "launch", "event": "a",
         "ts": "2030-01-01T00:00:00Z", "data": [3]},
        {"worktree_id": ["wt"], "launch_id": "launch", "event": "a"},
        {"worktree_id": "other", "launch_id": "launch", "event": "a"},
    ]
    path.write_text("".join(json.dumps(rec) + "\n" for rec in records), encoding="utf-8")
    monkeypatch.setattr(activity, "log_path", lambda: path)
    filters = {"worktree_id": "wt", "launch_id": "launch", "event": "a"}
    assert activity.read_events(**filters) == records[:3]
    assert activity.read_events(**filters, limit=0) == records[:3]
    assert activity.read_events(**filters, limit=-1) == records[:3]
    assert activity.read_events(**filters, limit=2) == records[1:3]
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert activity.read_events(**filters, since=since) == records[1:3]
    result = activity.read_events(**filters, since=since, limit=1)
    assert result == records[2:3]
    result[0]["data"].append(4)
    assert activity.read_events(**filters, limit=1) == records[2:3]
    assert activity.read_events(worktree_id="", event="") == records


def test_concurrent_query_values_share_index_without_cross_contamination(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("".join(
        json.dumps({"event": str(i % 4), "n": i}) + "\n" for i in range(100)
    ), encoding="utf-8")
    def query(i):
        return list(jsonl_cache.read_jsonl(path, match={"event": str(i % 4)}))
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, result in enumerate(pool.map(query, range(100))):
            assert [r["n"] for r in result] == list(range(i % 4, 100, 4))


def test_index_field_sets_are_bounded_and_order_independent(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    path.write_text('{"a":"x","b":"y","c":"z"}\n', encoding="utf-8")
    monkeypatch.setattr(jsonl_cache, "_MAX_INDEXES", 2)
    assert len(jsonl_cache.read_jsonl(path, match={"a": "x", "b": "y"})) == 1
    entry = jsonl_cache._cache[(str(path), "strict")]
    first = entry.indexes[("a", "b")]
    assert len(jsonl_cache.read_jsonl(path, match={"b": "y", "a": "x"})) == 1
    assert entry.indexes[("a", "b")] is first
    jsonl_cache.read_jsonl(path, match={"a": "x"})
    jsonl_cache.read_jsonl(path, match={"b": "y"})
    assert len(entry.indexes) == 2
    assert ("a", "b") not in entry.indexes
