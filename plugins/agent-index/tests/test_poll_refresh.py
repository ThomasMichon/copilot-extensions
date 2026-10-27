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


def manifest(*, retain=True, token=None):
    return CrawlManifest(
        source=SOURCE, accounting_family=SOURCE,
        upserts=[FileEntry("one.md", "current", "markdown", SOURCE)],
        poll_refresh=PollRefresh(retain, {"one.md": token or uuid.uuid4().hex}),
    )


def test_hint_candidates_commit_with_manifest_and_survive_journal_completion(tmp_path):
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    hint = manifest()
    journal.save(hint, scope="account-a", full=False)
    raw = json.loads(journal.read_payload(SOURCE, "account-a"))
    assert raw["version"] == 3
    assert journal.load(SOURCE, "account-a") == (hint, False)
    journal.clear(SOURCE, manifest=hint, scope="account-a")
    restarted = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    assert restarted.load(SOURCE, "account-a") is None
    refresh = restarted.refresh_candidates(SOURCE, "account-a", SOURCE)
    assert refresh == PollRefresh(False, hint.poll_refresh.paths)
    with pytest.raises(PendingCrawlConflict, match="hinted candidates"):
        restarted.assert_gc_available()
    with pytest.raises(PendingCrawlConflict, match="hinted candidates"):
        restarted.assert_family_available("alias", SOURCE)
    restarted.assert_family_available("other", "git:unrelated")
    for scope, family in (("other-account", SOURCE), ("account-a", "git:other")):
        with pytest.raises(ValueError, match="different source/account"):
            restarted.refresh_candidates(SOURCE, scope, family)


def test_ordinary_completion_clears_only_the_captured_candidate_generation(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    first = manifest()
    journal.save(first, scope="scope", full=False)
    journal.clear(SOURCE, manifest=first, scope="scope")
    captured = journal.refresh_candidates(SOURCE, "scope", SOURCE)
    newer = manifest()
    journal.save(newer, scope="scope", full=False)
    journal.clear(SOURCE, manifest=newer, scope="scope")
    old_poll = manifest(retain=False, token=captured.paths["one.md"])
    journal.save(old_poll, scope="scope", full=False)
    journal.clear(SOURCE, manifest=old_poll, scope="scope")
    assert journal.refresh_candidates(SOURCE, "scope", SOURCE).paths == newer.poll_refresh.paths
    current_poll = manifest(retain=False, token=newer.poll_refresh.paths["one.md"])
    journal.save(current_poll, scope="scope", full=False)
    journal.clear(SOURCE, manifest=current_poll, scope="scope")
    assert journal.refresh_candidates(SOURCE, "scope", SOURCE) is None
    journal.assert_gc_available()


def test_stale_completion_cannot_clear_a_replacement_pending_manifest(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    old, replacement = manifest(), manifest()
    journal.save(old, scope="scope", full=False)
    journal.clear(SOURCE, manifest=old, scope="scope")
    journal.save(replacement, scope="scope", full=False)
    with pytest.raises(PendingCrawlConflict, match="changed before completion"):
        journal.clear(SOURCE, manifest=old, scope="scope")
    assert journal.load(SOURCE, "scope") == (replacement, False)


def test_missing_candidate_content_cannot_authorize_refresh_coverage(tmp_path):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    incomplete = manifest(retain=False)
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


def test_candidate_retention_and_pending_intent_roll_back_together(tmp_path, monkeypatch):
    journal = PendingCrawls(PathIndex(tmp_path / "paths.db"))
    retain = journal.retain_refresh

    def failed(conn, value, scope):
        retain(conn, value, scope)
        raise RuntimeError("interrupted transaction")

    monkeypatch.setattr(journal, "retain_refresh", failed)
    with pytest.raises(RuntimeError, match="interrupted transaction"):
        journal.save(manifest(), scope="scope", full=False)
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
