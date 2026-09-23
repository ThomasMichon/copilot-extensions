"""Unit tests for :mod:`agent_logger.catalog` -- the SQLite-backed
``(repo, pr_number) -> sessions`` index derived from review-annotation
sidecars.
"""

from __future__ import annotations

from pathlib import Path

from agent_logger.catalog import ReviewCatalogIndex, rebuild_from_sidecars
from agent_logger.sessions import write_review_annotation


def test_query_empty_catalog_returns_nothing(tmp_path: Path) -> None:
    index = ReviewCatalogIndex(tmp_path / "catalog.db")

    assert index.query("example/repo", 6100) == []


def test_record_then_query_single_match(tmp_path: Path) -> None:
    index = ReviewCatalogIndex(tmp_path / "catalog.db")

    index.record(
        session_id="s1",
        repo="example/repo",
        pr_number=6100,
        role="reviewer",
        recorded_at="2026-09-22T19:46:00Z",
    )

    entries = index.query("example/repo", 6100)

    assert len(entries) == 1
    assert entries[0].session_id == "s1"
    assert entries[0].role == "reviewer"


def test_query_multiple_matches_ordered_oldest_first(tmp_path: Path) -> None:
    index = ReviewCatalogIndex(tmp_path / "catalog.db")

    index.record(
        session_id="s-later", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T20:00:00Z",
    )
    index.record(
        session_id="s-earlier", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T19:00:00Z",
    )

    entries = index.query("example/repo", 6100)

    assert [e.session_id for e in entries] == ["s-earlier", "s-later"]


def test_query_is_scoped_by_repo_and_pr_number(tmp_path: Path) -> None:
    index = ReviewCatalogIndex(tmp_path / "catalog.db")

    index.record(
        session_id="s1", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T19:00:00Z",
    )
    index.record(
        session_id="s2", repo="example/repo", pr_number=6200,
        role="reviewer", recorded_at="2026-09-22T19:00:00Z",
    )
    index.record(
        session_id="s3", repo="other/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T19:00:00Z",
    )

    entries = index.query("example/repo", 6100)

    assert [e.session_id for e in entries] == ["s1"]


def test_query_time_window_filtering(tmp_path: Path) -> None:
    index = ReviewCatalogIndex(tmp_path / "catalog.db")

    index.record(
        session_id="s-early", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T18:00:00Z",
    )
    index.record(
        session_id="s-mid", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T19:00:00Z",
    )
    index.record(
        session_id="s-late", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T20:00:00Z",
    )

    entries = index.query(
        "example/repo", 6100,
        since="2026-09-22T18:30:00Z", until="2026-09-22T19:30:00Z",
    )

    assert [e.session_id for e in entries] == ["s-mid"]


def test_record_is_idempotent_on_duplicate_key(tmp_path: Path) -> None:
    index = ReviewCatalogIndex(tmp_path / "catalog.db")

    index.record(
        session_id="s1", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T19:00:00Z",
    )
    index.record(
        session_id="s1", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T19:00:00Z",
    )

    assert len(index.query("example/repo", 6100)) == 1


def test_write_review_annotation_populates_index_when_given(
    tmp_path: Path,
) -> None:
    session_dir = tmp_path / "session-state" / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text("", encoding="utf-8")
    index = ReviewCatalogIndex(tmp_path / "catalog.db")

    write_review_annotation(
        session_dir, repo="example/repo", pr_number=6100, index=index,
    )

    entries = index.query("example/repo", 6100)
    assert len(entries) == 1
    assert entries[0].session_id == "s1"


def test_write_review_annotation_index_populated_on_duplicate_call_too(
    tmp_path: Path,
) -> None:
    """A second call with an already-recorded (repo, pr, role) still records
    into the index -- e.g. a caller that adds `index=` to an existing write
    path only later should still backfill the index via ordinary re-calls."""
    session_dir = tmp_path / "session-state" / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text("", encoding="utf-8")

    write_review_annotation(session_dir, repo="example/repo", pr_number=6100)

    index = ReviewCatalogIndex(tmp_path / "catalog.db")
    write_review_annotation(
        session_dir, repo="example/repo", pr_number=6100, index=index,
    )

    entries = index.query("example/repo", 6100)
    assert len(entries) == 1
    assert entries[0].session_id == "s1"


def test_rebuild_from_sidecars_populates_index(tmp_path: Path) -> None:
    state_root = tmp_path / "session-state"
    for sid in ("s1", "s2"):
        d = state_root / sid
        d.mkdir(parents=True)
        (d / "events.jsonl").write_text("", encoding="utf-8")
        write_review_annotation(d, repo="example/repo", pr_number=6100)

    index = ReviewCatalogIndex(tmp_path / "catalog.db")
    scanned = rebuild_from_sidecars(index, state_root)

    assert scanned == 2
    entries = index.query("example/repo", 6100)
    assert {e.session_id for e in entries} == {"s1", "s2"}


def test_rebuild_from_sidecars_is_additive_and_idempotent(tmp_path: Path) -> None:
    state_root = tmp_path / "session-state"
    d = state_root / "s1"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("", encoding="utf-8")
    write_review_annotation(d, repo="example/repo", pr_number=6100)

    index = ReviewCatalogIndex(tmp_path / "catalog.db")
    index.record(
        session_id="s-preexisting", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T00:00:00Z",
    )

    rebuild_from_sidecars(index, state_root)
    rebuild_from_sidecars(index, state_root)  # idempotent re-run

    entries = index.query("example/repo", 6100)
    assert {e.session_id for e in entries} == {"s1", "s-preexisting"}


def test_rebuild_from_sidecars_skips_malformed_entries(tmp_path: Path) -> None:
    state_root = tmp_path / "session-state"
    d = state_root / "s1"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text("", encoding="utf-8")
    (d / "review-annotations.json").write_text(
        '[{"repo": "example/repo", "pr_number": "not-an-int", '
        '"role": "reviewer", "recorded_at": "2026-09-22T00:00:00Z"}]',
        encoding="utf-8",
    )

    index = ReviewCatalogIndex(tmp_path / "catalog.db")
    scanned = rebuild_from_sidecars(index, state_root)

    assert scanned == 1
    assert index.query("example/repo", 6100) == []
