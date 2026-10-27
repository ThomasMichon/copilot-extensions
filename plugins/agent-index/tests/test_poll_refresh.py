"""Pending-crawl candidate retention is durable, scoped and generation-fenced."""

from __future__ import annotations

import json
import uuid

import pytest

from agent_index.indexing.manifest import CrawlManifest, PollRefresh
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.sources.base import FileEntry

SOURCE = "git:example"


def manifest(*, retain=True, token=None, kind="file"):
    candidates = {"one.md": token or uuid.uuid4().hex}
    return CrawlManifest(
        source=SOURCE, accounting_family=SOURCE,
        upserts=[FileEntry("one.md", "current", "markdown", SOURCE + (":commits" if kind == "history" else ""))],
        poll_refresh=PollRefresh(
            retain, candidates if kind == "file" else {}, candidates if kind == "history" else {},
        ),
    )


@pytest.mark.parametrize("kind", ["file", "history"])
def test_hint_candidates_commit_with_manifest_and_survive_journal_completion(tmp_path, kind):
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    hint = manifest(kind=kind)
    journal.save(hint, scope="account-a", full=False)
    raw = json.loads(journal.read_payload(SOURCE, "account-a"))
    assert raw["version"] == 3
    assert journal.load(SOURCE, "account-a") == (hint, False)
    journal.clear(SOURCE, manifest=hint, scope="account-a")
    restarted = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    assert restarted.load(SOURCE, "account-a") is None
    refresh = restarted.refresh_candidates(SOURCE, "account-a", SOURCE)
    assert refresh == PollRefresh(False, hint.poll_refresh.paths, hint.poll_refresh.history)
    assert restarted.sources() == [SOURCE]
    with pytest.raises(PendingCrawlConflict, match="hinted candidates"):
        restarted.assert_gc_available()
    with pytest.raises(PendingCrawlConflict, match="hinted candidates"):
        restarted.assert_family_available("alias", SOURCE)
    restarted.assert_family_available("other", "git:unrelated")
    for scope, family in (("other-account", SOURCE), ("account-a", "git:other")):
        with pytest.raises(ValueError, match="different source/account"):
            restarted.refresh_candidates(SOURCE, scope, family)


@pytest.mark.parametrize("kind", ["file", "history"])
def test_ordinary_completion_clears_only_the_captured_candidate_generation(tmp_path, kind):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    first = manifest(kind=kind)
    journal.save(first, scope="scope", full=False)
    journal.clear(SOURCE, manifest=first, scope="scope")
    captured = journal.refresh_candidates(SOURCE, "scope", SOURCE)
    newer = manifest(kind=kind)
    journal.save(newer, scope="scope", full=False)
    journal.clear(SOURCE, manifest=newer, scope="scope")
    captured_tokens = captured.history if kind == "history" else captured.paths
    old_poll = manifest(retain=False, token=captured_tokens["one.md"], kind=kind)
    journal.save(old_poll, scope="scope", full=False)
    journal.clear(SOURCE, manifest=old_poll, scope="scope")
    remaining = journal.refresh_candidates(SOURCE, "scope", SOURCE)
    assert remaining == PollRefresh(False, newer.poll_refresh.paths, newer.poll_refresh.history)
    tokens = newer.poll_refresh.history if kind == "history" else newer.poll_refresh.paths
    current_poll = manifest(retain=False, token=tokens["one.md"], kind=kind)
    journal.save(current_poll, scope="scope", full=False)
    journal.clear(SOURCE, manifest=current_poll, scope="scope")
    assert journal.refresh_candidates(SOURCE, "scope", SOURCE) is None
    journal.assert_gc_available()


@pytest.mark.parametrize("kind", ["file", "history"])
def test_stale_completion_cannot_clear_a_replacement_pending_manifest(tmp_path, kind):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    old, replacement = manifest(kind=kind), manifest(kind=kind)
    journal.save(old, scope="scope", full=False)
    journal.clear(SOURCE, manifest=old, scope="scope")
    journal.save(replacement, scope="scope", full=False)
    with pytest.raises(PendingCrawlConflict, match="changed before completion"):
        journal.clear(SOURCE, manifest=old, scope="scope")
    assert journal.load(SOURCE, "scope") == (replacement, False)


@pytest.mark.parametrize("kind", ["file", "history"])
def test_missing_candidate_content_cannot_authorize_refresh_coverage(tmp_path, kind):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    incomplete = manifest(retain=False, kind=kind)
    incomplete.upserts = []
    with pytest.raises(ValueError, match="every retained candidate"):
        journal.save(incomplete, scope="scope", full=False)
    assert journal.sources() == []


def test_ordinary_manifest_keeps_the_existing_v1_serialization(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    plain = CrawlManifest(
        source=SOURCE, upserts=[FileEntry("one.md", "ordinary", "markdown", SOURCE)],
    )
    journal.save(plain, scope="scope", full=False)
    raw = json.loads(journal.read_payload(SOURCE, "scope"))
    assert raw["version"] == 1 and "poll_refresh" not in raw["manifest"]
    assert journal.load(SOURCE, "scope") == (plain, False)


@pytest.mark.parametrize("kind", ["file", "history"])
def test_candidate_retention_and_pending_intent_roll_back_together(tmp_path, monkeypatch, kind):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    retain = journal.retain_refresh

    def failed(conn, value, scope):
        retain(conn, value, scope)
        raise RuntimeError("interrupted transaction")

    monkeypatch.setattr(journal, "retain_refresh", failed)
    with pytest.raises(RuntimeError, match="interrupted transaction"):
        journal.save(manifest(kind=kind), scope="scope", full=False)
    assert journal.load(SOURCE, "scope") is None
    assert journal.refresh_candidates(SOURCE, "scope", SOURCE) is None


def test_v1_envelope_cannot_silently_drop_required_refresh_metadata(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    journal.save(manifest(), scope="scope", full=False)
    raw = json.loads(journal.read_payload(SOURCE, "scope"))
    raw["version"] = 1
    with journal._paths._connect() as conn:
        conn.execute("UPDATE pending_crawls SET payload = ?", (json.dumps(raw),))
    with pytest.raises(ValueError, match="expanded journal version"):
        journal.load(SOURCE, "scope")


@pytest.mark.parametrize("missing", ["retain", "paths", "history"])
def test_v3_refresh_metadata_is_required_even_when_one_candidate_kind_is_empty(tmp_path, missing):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    journal.save(manifest(kind="history"), scope="scope", full=False)
    raw = json.loads(journal.read_payload(SOURCE, "scope"))
    raw["manifest"]["poll_refresh"].pop(missing)
    with journal._paths._connect() as conn:
        conn.execute("UPDATE pending_crawls SET payload = ?", (json.dumps(raw),))
    with pytest.raises(ValueError, match="polling-refresh metadata"):
        journal.load(SOURCE, "scope")


def test_file_and_history_with_same_path_keep_independent_generations_and_coverage(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    file, history = manifest(), manifest(kind="history")
    combined = CrawlManifest(
        source=SOURCE, accounting_family=SOURCE, upserts=file.upserts + history.upserts,
        poll_refresh=PollRefresh(True, file.poll_refresh.paths, history.poll_refresh.history),
    )
    journal.save(combined, scope="scope", full=False)
    journal.clear(SOURCE, manifest=combined, scope="scope")
    captured = journal.refresh_candidates(SOURCE, "scope", SOURCE)
    assert captured == PollRefresh(False, file.poll_refresh.paths, history.poll_refresh.history)
    wrong_family = manifest(retain=False, token=history.poll_refresh.history["one.md"])
    wrong_family.poll_refresh = PollRefresh(False, {}, history.poll_refresh.history)
    with pytest.raises(ValueError, match="every retained candidate"):
        journal.save(wrong_family, scope="scope", full=False)
    poll = manifest(retain=False, token=file.poll_refresh.paths["one.md"])
    journal.save(poll, scope="scope", full=False)
    journal.clear(SOURCE, manifest=poll, scope="scope")
    assert journal.refresh_candidates(SOURCE, "scope", SOURCE) == PollRefresh(
        False, {}, history.poll_refresh.history,
    )
