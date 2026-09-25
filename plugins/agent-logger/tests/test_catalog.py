"""Unit tests for :mod:`agent_logger.catalog` -- the SQLite-backed
``(repo, pr_number) -> sessions`` index derived from review-annotation
sidecars.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


# --- default_index() + production-path wiring ------------------------------

def test_default_index_resolves_configured_db_path(tmp_path: Path) -> None:
    from agent_logger.catalog import default_index

    class _StubConfig:
        catalog_db_path = tmp_path / "custom-catalog.db"

    index = default_index(cfg=_StubConfig())

    assert index.db_path == tmp_path / "custom-catalog.db"
    # Usable immediately (migration already ran).
    assert index.query("example/repo", 6100) == []


def test_ordinary_write_then_rebuild_then_query_end_to_end(tmp_path: Path) -> None:
    """The exact gap flagged in review: an ordinary write_review_annotation()
    call with no `index=` must still become queryable once `catalog rebuild`
    (rebuild_from_sidecars) runs -- proving the production annotation path
    doesn't require every writer to pass `index=` to eventually be indexed."""
    from agent_logger.catalog import ReviewCatalogIndex, rebuild_from_sidecars

    state_root = tmp_path / "session-state"
    session_dir = state_root / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text("", encoding="utf-8")

    # No `index=` passed -- exactly today's real writer shape.
    write_review_annotation(session_dir, repo="example/repo", pr_number=6100)

    index = ReviewCatalogIndex(tmp_path / "catalog.db")
    rebuild_from_sidecars(index, state_root)

    entries = index.query("example/repo", 6100)
    assert len(entries) == 1
    assert entries[0].session_id == "s1"


def test_write_review_annotation_with_default_index_populates_immediately(
    tmp_path: Path,
) -> None:
    """The other supported shape: a caller passes `index=default_index()`
    directly, so the catalog is populated at write time with no rebuild."""
    from agent_logger.catalog import ReviewCatalogIndex, default_index

    session_dir = tmp_path / "session-state" / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text("", encoding="utf-8")

    class _StubConfig:
        catalog_db_path = tmp_path / "catalog.db"

    index = default_index(cfg=_StubConfig())
    write_review_annotation(
        session_dir, repo="example/repo", pr_number=6100, index=index,
    )

    # A fresh handle onto the same db file sees the write (proves it's a
    # real durable index, not an in-process-only artifact).
    fresh = ReviewCatalogIndex(tmp_path / "catalog.db")
    entries = fresh.query("example/repo", 6100)
    assert len(entries) == 1
    assert entries[0].session_id == "s1"


# --------------------------------------------------------------------------- #
# CLI: `agent-logger annotate` -- the cross-repo process-boundary write path #
# --------------------------------------------------------------------------- #


def _run_annotate_cli(monkeypatch, tmp_path: Path, argv: list[str]) -> int:
    """Invoke the real CLI entry point with an isolated HOME/AGENT_LOGGER_HOME,
    matching how ``find_copilot_dir``/``load_config`` resolve in production --
    a session-fetch-style caller in another repository has no Python import
    path into agent-logger, only this process boundary."""
    from agent_logger import __main__ as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path / ".agent-logger"))
    return cli.main(argv)


def test_cli_annotate_writes_sidecar_and_populates_catalog(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from agent_logger.catalog import ReviewCatalogIndex
    from agent_logger.sessions import SessionRef, read_review_annotations

    session_dir = tmp_path / ".copilot" / "session-state" / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text("", encoding="utf-8")

    rc = _run_annotate_cli(
        monkeypatch,
        tmp_path,
        [
            "annotate",
            "s1",
            "--repo",
            "example/repo",
            "--pr-number",
            "6100",
        ],
    )

    assert rc == 0
    entries = read_review_annotations(SessionRef(id="s1", kind="live", path=session_dir))
    assert entries[0]["repo"] == "example/repo"
    assert entries[0]["pr_number"] == 6100

    index = ReviewCatalogIndex(tmp_path / ".agent-logger" / "review-catalog.db")
    assert len(index.query("example/repo", 6100)) == 1

    payload = capsys.readouterr().out
    assert "s1" in payload
    assert "example/repo" in payload


def test_cli_annotate_missing_session_exits_nonzero(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".copilot" / "session-state").mkdir(parents=True)

    rc = _run_annotate_cli(
        monkeypatch,
        tmp_path,
        [
            "annotate",
            "does-not-exist",
            "--repo",
            "example/repo",
            "--pr-number",
            "6100",
        ],
    )

    assert rc != 0


def test_cli_annotate_rejects_directory_without_events_marker(
    monkeypatch, tmp_path: Path
) -> None:
    """A real directory under session-state that never held a session (no
    events.jsonl) must not be treated as a live session -- matching
    sessions.resolve_ref()'s own contract, and never getting a
    review-annotations.json sidecar written into an unrelated directory."""
    not_a_session = tmp_path / ".copilot" / "session-state" / "not-a-session"
    not_a_session.mkdir(parents=True)

    rc = _run_annotate_cli(
        monkeypatch,
        tmp_path,
        [
            "annotate",
            "not-a-session",
            "--repo",
            "example/repo",
            "--pr-number",
            "6100",
        ],
    )

    assert rc != 0
    assert not (not_a_session / "review-annotations.json").exists()


def test_cli_annotate_reports_out_of_range_pr_number_as_clear_error(
    monkeypatch, tmp_path: Path
) -> None:
    """A --pr-number beyond SQLite's 64-bit INTEGER range raises
    OverflowError binding the catalog insert -- must surface as the promised
    error: message, never an uncaught traceback."""
    session_dir = tmp_path / ".copilot" / "session-state" / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text("", encoding="utf-8")

    rc = _run_annotate_cli(
        monkeypatch,
        tmp_path,
        [
            "annotate",
            "s1",
            "--repo",
            "example/repo",
            "--pr-number",
            str(2**63),
        ],
    )

    assert rc != 0


def test_cli_annotate_rejects_unsafe_session_id(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / ".copilot" / "session-state").mkdir(parents=True)

    rc = _run_annotate_cli(
        monkeypatch,
        tmp_path,
        [
            "annotate",
            "../escape",
            "--repo",
            "example/repo",
            "--pr-number",
            "6100",
        ],
    )

    assert rc != 0
    # Never even attempted to escape session-state and land a sidecar there.
    assert not (tmp_path / ".copilot" / "escape").exists()


def test_cli_annotate_rejects_symlinked_session_directory(
    monkeypatch, tmp_path: Path
) -> None:
    state_root = tmp_path / ".copilot" / "session-state"
    state_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (state_root / "s1").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")

    rc = _run_annotate_cli(
        monkeypatch,
        tmp_path,
        [
            "annotate",
            "s1",
            "--repo",
            "example/repo",
            "--pr-number",
            "6100",
        ],
    )

    assert rc != 0


# --------------------------------------------------------------------------- #
# CLI: `agent-logger catalog query` -- the cross-repo process-boundary read  #
# side fallback tier (Intelligence Dampener's reviewer-link chain, Phase 3) #
# --------------------------------------------------------------------------- #


def _run_query_cli(monkeypatch, tmp_path: Path, argv: list[str]) -> tuple[int, str]:
    """Invoke the real CLI entry point with an isolated HOME/AGENT_LOGGER_HOME
    and capture stdout, mirroring ``_run_annotate_cli`` -- a caller in another
    repository (Intelligence Dampener) has no Python import path into
    agent-logger, only this process boundary."""
    import contextlib
    import io

    from agent_logger import __main__ as cli

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path / ".agent-logger"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def test_cli_query_empty_catalog_returns_empty_list(monkeypatch, tmp_path: Path) -> None:
    import json as _json

    rc, out = _run_query_cli(
        monkeypatch,
        tmp_path,
        ["catalog", "query", "--repo", "example/repo", "--pr-number", "6100"],
    )

    assert rc == 0
    payload = _json.loads(out)
    assert payload == {"repo": "example/repo", "pr_number": 6100, "sessions": []}


def test_cli_query_resolves_annotated_live_session(monkeypatch, tmp_path: Path) -> None:
    import json as _json

    session_dir = tmp_path / ".copilot" / "session-state" / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text("", encoding="utf-8")

    rc = _run_annotate_cli(
        monkeypatch,
        tmp_path,
        ["annotate", "s1", "--repo", "example/repo", "--pr-number", "6100"],
    )
    assert rc == 0

    rc, out = _run_query_cli(
        monkeypatch,
        tmp_path,
        ["catalog", "query", "--repo", "example/repo", "--pr-number", "6100"],
    )

    assert rc == 0
    payload = _json.loads(out)
    assert payload["sessions"] == [{"session_id": "s1", "kind": "live"}]


def test_cli_query_is_scoped_by_repo_and_pr_number(monkeypatch, tmp_path: Path) -> None:
    import json as _json

    session_dir = tmp_path / ".copilot" / "session-state" / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text("", encoding="utf-8")
    _run_annotate_cli(
        monkeypatch,
        tmp_path,
        ["annotate", "s1", "--repo", "example/repo", "--pr-number", "6100"],
    )

    rc, out = _run_query_cli(
        monkeypatch,
        tmp_path,
        ["catalog", "query", "--repo", "example/repo", "--pr-number", "9999"],
    )

    assert rc == 0
    assert _json.loads(out)["sessions"] == []


def test_cli_query_skips_session_unresolvable_on_this_host(
    monkeypatch, tmp_path: Path
) -> None:
    """A catalog entry whose session this host cannot resolve (e.g. it lives
    only on another machine's corpus) is silently skipped, matching
    ``query_reviewer_sessions``'s own contract -- never an error."""
    import json as _json

    from agent_logger.catalog import default_index
    from agent_logger.config import load_config

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path / ".agent-logger"))
    (tmp_path / ".copilot" / "session-state").mkdir(parents=True)

    cfg = load_config(include_repo=False)
    index = default_index(cfg)
    index.record(
        session_id="ghost-session",
        repo="example/repo",
        pr_number=6100,
        role="reviewer",
        recorded_at="2026-09-24T00:00:00Z",
    )

    rc, out = _run_query_cli(
        monkeypatch,
        tmp_path,
        ["catalog", "query", "--repo", "example/repo", "--pr-number", "6100"],
    )

    assert rc == 0
    assert _json.loads(out)["sessions"] == []
