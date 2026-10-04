"""Tests for :mod:`claim_history_mirror` (worktree-claims-transitive-finalization
Phase 3b's remote-mirroring item): the git-ref append-only mirror for
:mod:`claim_history`'s local ownership ledger, its opt-in sync sweep, and
the ``claims history <ref> --remote`` read path.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agent_worktrees import claim_history, claim_history_mirror
from agent_worktrees.lease_config import LeaseSettings


def git(
    *args: str,
    cwd: Path | None = None,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "mirror-test",
            "GIT_AUTHOR_EMAIL": "mirror-test@example.invalid",
            "GIT_COMMITTER_NAME": "mirror-test",
            "GIT_COMMITTER_EMAIL": "mirror-test@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "store.git"
    git("init", "--bare", str(path))
    return path


@pytest.fixture
def settings(remote: Path) -> LeaseSettings:
    return LeaseSettings(
        origin=str(remote),
        ref_prefix=claim_history_mirror.DEFAULT_REF_PREFIX,
        default_ttl_seconds=60,
        max_ttl_seconds=3600,
    )


def mirror(settings: LeaseSettings) -> claim_history_mirror.ClaimHistoryMirror:
    return claim_history_mirror.ClaimHistoryMirror(
        settings, sleep=lambda _seconds: None, jitter=lambda _low, _high: 0,
    )


# ── ClaimHistoryMirror.push / .fetch ─────────────────────────────────────

def test_push_then_fetch_round_trips_one_entry(settings: LeaseSettings):
    m = mirror(settings)
    entry = {
        "ts": "2026-10-03T12:00:00+00:00", "kind": "pr", "ref": "o/r#1",
        "worktree_id": "wt-a", "machine": "m1", "event": "claimed",
        "session_id": "sess-1", "note": "opened",
    }
    m.push(entry)
    fetched = m.fetch("pr", "o/r#1")
    assert len(fetched) == 1
    assert fetched[0]["ref"] == "o/r#1"
    assert fetched[0]["event"] == "claimed"
    assert fetched[0]["session_id"] == "sess-1"
    assert fetched[0]["note"] == "opened"


def test_push_appends_rather_than_overwrites(settings: LeaseSettings):
    m = mirror(settings)
    m.push({
        "ts": "2026-10-03T12:00:00+00:00", "kind": "pr", "ref": "o/r#1",
        "worktree_id": "wt-a", "machine": "m1", "event": "claimed",
    })
    m.push({
        "ts": "2026-10-03T13:00:00+00:00", "kind": "pr", "ref": "o/r#1",
        "worktree_id": "wt-a", "machine": "m1", "event": "released",
    })
    fetched = m.fetch("pr", "o/r#1")
    assert [e["event"] for e in fetched] == ["claimed", "released"]


def test_fetch_is_empty_for_a_never_mirrored_ref(settings: LeaseSettings):
    assert mirror(settings).fetch("pr", "o/r#404") == []


def test_fetch_is_scoped_to_its_own_resource(settings: LeaseSettings):
    m = mirror(settings)
    m.push({
        "ts": "2026-10-03T12:00:00+00:00", "kind": "pr", "ref": "o/r#1",
        "worktree_id": "wt-a", "machine": "m1", "event": "claimed",
    })
    assert mirror(settings).fetch("pr", "o/r#2") == []


# ── sync_pending ──────────────────────────────────────────────────────────

def test_sync_pending_is_unavailable_with_no_store_configured(monkeypatch):
    monkeypatch.setattr(claim_history_mirror, "mirror_settings", lambda origin=None: None)
    result = claim_history_mirror.sync_pending()
    assert result == {"available": False, "pushed": 0, "refs": []}


def test_sync_pending_pushes_every_locally_recorded_event(settings: LeaseSettings, monkeypatch):
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="released",
    )
    claim_history.record_event(
        kind="pr", ref="o/r#2", worktree_id="wt-b", machine="m1", event="claimed",
    )

    result = claim_history_mirror.sync_pending()
    assert result["available"] is True
    assert result["pushed"] == 3

    remote_r1 = mirror(settings).fetch("pr", "o/r#1")
    remote_r2 = mirror(settings).fetch("pr", "o/r#2")
    assert [e["event"] for e in remote_r1] == ["claimed", "released"]
    assert [e["event"] for e in remote_r2] == ["claimed"]


def test_sync_pending_is_idempotent_and_resumable(settings: LeaseSettings, monkeypatch):
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    first = claim_history_mirror.sync_pending()
    assert first["pushed"] == 1

    # A second run with nothing new pending pushes nothing further.
    second = claim_history_mirror.sync_pending()
    assert second["pushed"] == 0

    # A new event appended afterward is picked up on the next run, without
    # re-pushing the already-mirrored one.
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="released",
    )
    third = claim_history_mirror.sync_pending()
    assert third["pushed"] == 1

    fetched = mirror(settings).fetch("pr", "o/r#1")
    assert [e["event"] for e in fetched] == ["claimed", "released"]


def test_sync_pending_dry_run_reports_without_pushing(settings: LeaseSettings, monkeypatch):
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    result = claim_history_mirror.sync_pending(dry_run=True)
    assert result["refs"] == [{"ref": "o/r#1", "kind": "pr", "pending": 1}]
    assert mirror(settings).fetch("pr", "o/r#1") == []


def test_sync_pending_ignores_an_unsupported_kind_never_recorded_locally(
    settings: LeaseSettings, monkeypatch
):
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    # An unsupported kind is never appended by claim_history.record_event in
    # the first place (SUPPORTED_KINDS), so there is nothing for this sweep
    # to ever find for it.
    claim_history.record_event(
        kind="codespace", ref="cs-1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    result = claim_history_mirror.sync_pending()
    assert result["pushed"] == 0
    assert result["refs"] == []


# ── fetch_remote_history (the claims history --remote read path) ────────

def test_fetch_remote_history_is_empty_with_no_store_configured(monkeypatch):
    monkeypatch.setattr(claim_history_mirror, "mirror_settings", lambda origin=None: None)
    assert claim_history_mirror.fetch_remote_history("o/r#1") == []


def test_fetch_remote_history_returns_mirrored_events(settings: LeaseSettings, monkeypatch):
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    claim_history_mirror.sync_pending()
    fetched = claim_history_mirror.fetch_remote_history("o/r#1")
    assert len(fetched) == 1
    assert fetched[0]["event"] == "claimed"
