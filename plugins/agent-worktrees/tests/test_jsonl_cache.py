"""Tests for :mod:`jsonl_cache` -- the per-process memoization layer for
append-only JSONL log reads (``activity.read_events`` / ``handoff_trace.
read_trace``).

Mirrors ``test_record_cache.py``'s miss/hit and invalidation coverage, scoped
to a plain parsed ``list[dict]`` rather than a ``WorktreeRecord``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import jsonl_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    jsonl_cache.clear()
    yield
    jsonl_cache.clear()


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _line_count_parser(calls: list[Path]):
    def _parser(path: Path) -> list[str]:
        calls.append(path)
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    return _parser


def _dict_parser(calls: list[Path]):
    def _parser(path: Path) -> list[dict]:
        calls.append(path)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    return _parser


def test_cached_parse_returns_the_cache_s_own_list_by_reference(tmp_path: Path):
    """jsonl_cache's documented "Mutation isolation invariant": unlike
    ``record_cache.cached_load``, this module does NOT deep-copy on every
    hit (that would reintroduce an O(log size) cost on every cache hit for
    a 90k-line log, defeating the point) -- it returns its cached list by
    reference, and its own callers (``activity.read_events``,
    ``handoff_trace.read_trace``) are responsible for copying only the
    small, bounded subset they actually return. A caller of THIS function
    directly must not mutate what it gets back in place."""
    path = tmp_path / "log.jsonl"
    path.write_text('{"event": "a"}\n', encoding="utf-8")
    calls: list[Path] = []
    parser = _dict_parser(calls)

    first = jsonl_cache.cached_parse(path, parser)
    second = jsonl_cache.cached_parse(path, parser)
    assert first is second, "a cache hit returns the identical cached list object"


def test_miss_then_hit_does_not_reparse(tmp_path: Path):
    path = tmp_path / "log.jsonl"
    _write_lines(path, ["a", "b"])
    calls: list[Path] = []
    parser = _line_count_parser(calls)

    first = jsonl_cache.cached_parse(path, parser)
    second = jsonl_cache.cached_parse(path, parser)
    assert first == second == ["a", "b"]
    assert len(calls) == 1, "second call must be a cache hit, not a re-parse"


def test_file_growth_invalidates_the_cache(tmp_path: Path):
    """The dominant real shape for these two logs: append-only growth."""
    path = tmp_path / "log.jsonl"
    _write_lines(path, ["a"])
    calls: list[Path] = []
    parser = _line_count_parser(calls)

    first = jsonl_cache.cached_parse(path, parser)
    assert first == ["a"]
    _write_lines(path, ["a", "b"])
    second = jsonl_cache.cached_parse(path, parser)
    assert second == ["a", "b"]
    assert len(calls) == 2, "the append's size change must force a re-parse"


def test_file_rewrite_same_size_is_not_guaranteed_to_invalidate(tmp_path: Path):
    """Documents the (mtime_ns, size) invalidation key's one known gap (shared
    with ``record_cache``): a same-second, same-size in-place rewrite can
    alias the stamp on a filesystem with coarse mtime resolution. Neither
    cache claims to cover that case; real callers only ever append."""
    path = tmp_path / "log.jsonl"
    _write_lines(path, ["aa"])
    calls: list[Path] = []
    parser = _line_count_parser(calls)
    jsonl_cache.cached_parse(path, parser)
    assert len(calls) == 1


def test_missing_file_is_not_cached_as_a_permanent_empty_result(tmp_path: Path):
    path = tmp_path / "missing.jsonl"
    calls: list[Path] = []
    parser = _line_count_parser(calls)

    first = jsonl_cache.cached_parse(path, parser)
    assert first == []
    _write_lines(path, ["a"])
    second = jsonl_cache.cached_parse(path, parser)
    assert second == ["a"]
    assert len(calls) == 2, "a file created after a missing-file miss must be read"


def test_clear_drops_every_entry(tmp_path: Path):
    path = tmp_path / "log.jsonl"
    _write_lines(path, ["a"])
    calls: list[Path] = []
    parser = _line_count_parser(calls)
    jsonl_cache.cached_parse(path, parser)
    jsonl_cache.clear()
    jsonl_cache.cached_parse(path, parser)
    assert len(calls) == 2, "clear() must force the next call to re-parse"


def test_invalidate_drops_only_the_given_path(tmp_path: Path):
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    _write_lines(path_a, ["a"])
    _write_lines(path_b, ["b"])
    calls_a: list[Path] = []
    calls_b: list[Path] = []
    jsonl_cache.cached_parse(path_a, _line_count_parser(calls_a))
    jsonl_cache.cached_parse(path_b, _line_count_parser(calls_b))

    jsonl_cache.invalidate(path_a)
    jsonl_cache.cached_parse(path_a, _line_count_parser(calls_a))
    jsonl_cache.cached_parse(path_b, _line_count_parser(calls_b))
    assert len(calls_a) == 2, "the invalidated path must re-parse"
    assert len(calls_b) == 1, "an unrelated path's cache entry must be untouched"


def test_invalidate_a_never_cached_path_is_a_noop(tmp_path: Path):
    jsonl_cache.invalidate(tmp_path / "never-read.jsonl")  # must not raise


def test_without_invalidate_an_aliased_stamp_would_return_stale_data(tmp_path: Path):
    """The gap ``invalidate()`` exists to close: a cache entry whose
    stamp happens to match the CURRENT file's real stamp is
    indistinguishable from "unchanged" to ``cached_parse`` alone, even
    when the file's actual content differs -- a real caller
    (``activity._prune()`` rewriting ``activity.jsonl`` in place,
    ``handoff_trace.remove_trace()`` + a reused worktree id recreating the
    same path) must invalidate explicitly after a same-path replace/
    recreate rather than rely on the stamp to always differ. The stamp's
    own device+inode component makes a real occurrence of this vanishingly
    rare (see jsonl_cache's "Cross-process safety" note), but a forced
    collision still demonstrates why ``invalidate()`` exists as a backstop."""
    path = tmp_path / "log.jsonl"
    _write_lines(path, ["aa"])
    calls: list[Path] = []
    parser = _line_count_parser(calls)
    jsonl_cache.cached_parse(path, parser)

    _write_lines(path, ["bb"])
    # Force a cache entry stamped against the file's CURRENT real identity
    # but holding the OLD content -- simulating the collision a forced
    # same-path replace could in principle produce, which ``cached_parse``
    # alone cannot detect.
    with jsonl_cache._cache_lock:
        jsonl_cache._cache[str(path)] = (jsonl_cache._stamp(path), ["aa"])

    aliased = jsonl_cache.cached_parse(path, parser)
    assert aliased == ["aa"], "a stamp collision alone returns the stale cached content"
    assert len(calls) == 1, "that was a false HIT -- the parser must not have been called again"

    jsonl_cache.invalidate(path)
    fixed = jsonl_cache.cached_parse(path, parser)
    assert fixed == ["bb"], "invalidate() must force a real re-parse despite the aliased stamp"
    assert len(calls) == 2

