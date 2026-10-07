"""Tests for :mod:`jsonl_cache` -- the per-process memoization layer for
append-only JSONL log reads (``activity.read_events`` / ``handoff_trace.
read_trace``).

Mirrors ``test_record_cache.py``'s miss/hit and invalidation coverage, scoped
to a plain parsed ``list[dict]`` rather than a ``WorktreeRecord``.
"""

from __future__ import annotations

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
