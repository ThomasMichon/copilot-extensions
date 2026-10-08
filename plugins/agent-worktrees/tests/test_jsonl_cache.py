"""Incremental lifecycle-log correctness and append-heavy work bounds."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_worktrees import jsonl_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    jsonl_cache.clear()
    yield
    jsonl_cache.clear()


def _append(path: Path, raw: bytes) -> None:
    with path.open("ab") as handle:
        handle.write(raw)


def test_unchanged_snapshot_reuses_read_only_values(tmp_path, monkeypatch):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"event":"a"}\n')
    first = jsonl_cache.read_jsonl(path)
    monkeypatch.setattr(jsonl_cache, "_read_snapshot", lambda *args: pytest.fail("cache miss"))
    assert jsonl_cache.read_jsonl(path) is first


def test_append_work_is_linear_in_new_bytes_not_history(tmp_path, monkeypatch):
    path = tmp_path / "log.jsonl"
    line = b'{"event":"existing","nested":{"value":123}}\n'
    path.write_bytes(line * 100_000)
    jsonl_cache.read_jsonl(path)
    reads = []
    parses = []
    read = jsonl_cache._read_snapshot
    loads = json.loads

    def counted_read(handle, offset, size):
        reads.append(size - offset)
        return read(handle, offset, size)

    def counted_loads(raw):
        parses.append(raw)
        return loads(raw)

    monkeypatch.setattr(jsonl_cache, "_read_snapshot", counted_read)
    monkeypatch.setattr(jsonl_cache.json, "loads", counted_loads)
    for i in range(20):
        _append(path, line)
        assert len(jsonl_cache.read_jsonl(path)) == 100_001 + i
    assert reads == [len(line)] * 20
    assert len(parses) == 20


@pytest.mark.parametrize("initial", [b'{"event":"part', b'{"event":"valid"}'])
def test_unterminated_record_is_revisited_without_loss_or_duplicates(tmp_path, initial):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"event":"first"}\n' + initial)
    first = jsonl_cache.read_jsonl(path)
    assert first[0]["event"] == "first"
    ending = b'ial"}\n' if initial.endswith(b"part") else b'\n'
    _append(path, ending + b'{"event":"last"}\n')
    assert [e["event"] for e in jsonl_cache.read_jsonl(path)] == [
        "first", "partial" if initial.endswith(b"part") else "valid", "last",
    ]
    assert first[0]["event"] == "first"


def test_partial_utf8_at_eof_completes_on_append(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"event":"\xe2')
    assert jsonl_cache.read_jsonl(path) == []
    _append(path, b'\x82\xac"}\n')
    assert jsonl_cache.read_jsonl(path) == [{"event": "\u20ac"}]


def test_same_stamp_atomic_replacement_invalidates_without_reader_signal(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"event":"old"}\n')
    assert jsonl_cache.read_jsonl(path) == [{"event": "old"}]
    stamp = path.stat()
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(b'{"event":"new"}\n')
    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    replacement.replace(path)
    assert path.stat().st_size == stamp.st_size
    assert path.stat().st_mtime_ns == stamp.st_mtime_ns
    assert jsonl_cache.read_jsonl(path) == [{"event": "new"}]


def test_delete_then_recreate_does_not_inherit_events(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"event":"old"}\n')
    jsonl_cache.read_jsonl(path)
    path.unlink()
    assert jsonl_cache.read_jsonl(path) == []
    path.write_bytes(b'{"event":"new"}\n')
    assert jsonl_cache.read_jsonl(path) == [{"event": "new"}]


@pytest.mark.parametrize("new", [b'{}\n', b'{"event":"new"}\n'])
def test_truncation_or_same_size_rewrite_resets_reader(tmp_path, new):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"event":"old"}\n')
    jsonl_cache.read_jsonl(path)
    old = path.stat()
    path.write_bytes(new)
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
    assert jsonl_cache.read_jsonl(path) == [json.loads(new)]


def test_concurrent_append_cannot_extend_this_read_past_opening_size(tmp_path, monkeypatch):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"event":"first"}\n')
    read = jsonl_cache._read_snapshot
    calls = []

    def append_before_read(handle, offset, size):
        calls.append(size)
        _append(path, b'{"event":"next"}\n')
        return read(handle, offset, size)

    monkeypatch.setattr(jsonl_cache, "_read_snapshot", append_before_read)
    assert jsonl_cache.read_jsonl(path) == [{"event": "first"}]
    monkeypatch.setattr(jsonl_cache, "_read_snapshot", read)
    assert jsonl_cache.read_jsonl(path) == [{"event": "first"}, {"event": "next"}]
    assert len(calls) == 1


def test_replacement_between_stat_and_open_uses_opened_identity(tmp_path, monkeypatch):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"event":"first"}\n')
    jsonl_cache.read_jsonl(path)
    _append(path, b'{"event":"second"}\n')
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(b'{"event":"replacement"}\n')
    original_open = Path.open
    replaced = False

    def racing_open(self, *args, **kwargs):
        nonlocal replaced
        if self == path and not replaced:
            replaced = True
            replacement.replace(path)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing_open)
    assert jsonl_cache.read_jsonl(path) == [{"event": "replacement"}]


def test_decode_policy_isolated_and_malformed_lines_skipped(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'not json\n{"event":"ok","value":"a\xe2\x80\xa8b"}\n')
    assert jsonl_cache.read_jsonl(path) == [{"event": "ok", "value": "a\u2028b"}]
    _append(path, b'{"event":"bad \xff"}\n')
    with pytest.raises(UnicodeDecodeError):
        jsonl_cache.read_jsonl(path)
    assert jsonl_cache.read_jsonl(path, errors="replace")[-1]["event"] == "bad \ufffd"


def test_cursors_are_bounded_and_explicitly_invalidated(tmp_path, monkeypatch):
    monkeypatch.setattr(jsonl_cache, "_MAX_FILES", 2)
    paths = [tmp_path / f"{i}.jsonl" for i in range(3)]
    for path in paths:
        path.write_bytes(b'{}\n')
        jsonl_cache.read_jsonl(path)
    assert len(jsonl_cache._cache) == 2
    assert (str(paths[0]), "strict") not in jsonl_cache._cache
    jsonl_cache.invalidate(paths[1])
    assert len(jsonl_cache._cache) == 1
    jsonl_cache.clear()
    assert not jsonl_cache._cache
