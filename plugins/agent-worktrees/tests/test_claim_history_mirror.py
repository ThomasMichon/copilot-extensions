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
from agent_worktrees.lease_protocol import ProtocolError


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


class _AllowAllWorktrees:
    """Stand-in for :func:`claim_history_mirror._current_project_worktree_ids`
    when a test isn't exercising cross-project scoping itself -- membership
    always succeeds, so every test written before that guard existed keeps
    working unchanged."""

    def __contains__(self, _value: object) -> bool:
        return True


@pytest.fixture(autouse=True)
def _allow_all_worktrees(monkeypatch):
    monkeypatch.setattr(
        claim_history_mirror, "_current_project_worktree_ids",
        lambda: _AllowAllWorktrees(),
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


def test_push_preserves_an_empty_required_field(settings: LeaseSettings):
    """A legacy record can carry ``machine=""`` (``tracking.py``'s own
    degraded-attribution case) -- the serializer must keep it verbatim
    rather than treating an empty required field like an absent optional
    one, or the round-tripped entry fails its own required-field check."""
    m = mirror(settings)
    m.push({
        "ts": "2026-10-03T12:00:00+00:00", "kind": "pr", "ref": "o/r#1",
        "worktree_id": "wt-a", "machine": "", "event": "claimed",
    })
    fetched = m.fetch("pr", "o/r#1")
    assert len(fetched) == 1
    assert fetched[0]["machine"] == ""


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


def test_parse_entry_rejects_a_non_string_field():
    """A remote entry with a wrongly-typed field (e.g. ``note`` as a list)
    must be rejected at parse time -- never accepted and handed to a caller
    (``claims history --remote``'s own merge) that assumes every field is
    a plain string."""
    bad = claim_history_mirror._serialize_entry({
        "ts": "t", "kind": "pr", "ref": "r", "worktree_id": "w",
        "machine": "m", "event": "claimed",
    })
    # Splice in a non-string optional field the normal serializer would
    # never produce, simulating a malformed/foreign commit on the ref.
    import json as _json
    prefix, body = bad.split("\n", 1)
    payload = _json.loads(body)
    payload["note"] = ["not", "a", "string"]
    tampered = prefix + "\n" + _json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ProtocolError):
        claim_history_mirror._parse_entry(tampered)


# ── sync_pending ──────────────────────────────────────────────────────────

def test_sync_pending_is_unavailable_with_no_store_configured(monkeypatch):
    monkeypatch.setattr(claim_history_mirror, "mirror_settings", lambda origin=None: None)
    result = claim_history_mirror.sync_pending()
    assert result == {"available": False, "pushed": 0, "refs": [], "failed": []}


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
    assert result["failed"] == []

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


def test_sync_pending_recovers_from_a_checkpoint_write_failure(
    settings: LeaseSettings, monkeypatch
):
    """A push that lands on the remote but whose checkpoint write then
    fails (a crash, a full disk) must neither lose the pushed event nor
    duplicate it on the next, successful sweep."""
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    real_save_state = claim_history_mirror._save_state
    monkeypatch.setattr(
        claim_history_mirror, "_save_state",
        lambda _state: (_ for _ in ()).throw(OSError("disk full")),
    )

    first = claim_history_mirror.sync_pending()
    assert first["pushed"] == 1
    assert len(first["failed"]) == 1
    # The push itself landed, even though its checkpoint never did.
    assert [e["event"] for e in mirror(settings).fetch("pr", "o/r#1")] == ["claimed"]

    monkeypatch.setattr(claim_history_mirror, "_save_state", real_save_state)
    second = claim_history_mirror.sync_pending()
    assert second["pushed"] == 1
    assert second["failed"] == []
    # Still exactly one commit on the remote -- the retry recognized the
    # already-applied event rather than appending a duplicate.
    assert [e["event"] for e in mirror(settings).fetch("pr", "o/r#1")] == ["claimed"]


def test_sync_pending_scopes_the_cursor_by_destination_store(
    settings: LeaseSettings, tmp_path: Path, monkeypatch
):
    """Syncing the same local history to a SECOND store must not be
    short-circuited by the first store's own persisted cursor."""
    other_remote = tmp_path / "store-b.git"
    git("init", "--bare", str(other_remote))
    other_settings = LeaseSettings(
        origin=str(other_remote), ref_prefix=claim_history_mirror.DEFAULT_REF_PREFIX,
        default_ttl_seconds=60, max_ttl_seconds=3600,
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )

    monkeypatch.setattr(claim_history_mirror, "mirror_settings", lambda origin=None: settings)
    first = claim_history_mirror.sync_pending()
    assert first["pushed"] == 1

    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: other_settings
    )
    second = claim_history_mirror.sync_pending()
    assert second["pushed"] == 1
    assert [e["event"] for e in mirror(other_settings).fetch("pr", "o/r#1")] == ["claimed"]


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


def test_sync_pending_excludes_events_from_another_project(
    settings: LeaseSettings, monkeypatch
):
    """The local claim-history ledger is machine-global (shared across
    every project's worktrees), but a sweep only ever runs against ONE
    project's configured store -- an event whose worktree this project's
    own tracking records don't recognize must never ride along."""
    monkeypatch.setattr(
        claim_history_mirror, "_current_project_worktree_ids", lambda: {"wt-a"}
    )
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    claim_history.record_event(
        kind="pr", ref="o/r#2", worktree_id="wt-other-project", machine="m1", event="claimed",
    )

    result = claim_history_mirror.sync_pending()
    assert result["pushed"] == 1
    assert [r["ref"] for r in result["refs"]] == ["o/r#1"]
    assert mirror(settings).fetch("pr", "o/r#2") == []


def test_sync_pending_serializes_overlapping_sweeps(settings: LeaseSettings, monkeypatch):
    """The whole load/push/checkpoint cycle is wrapped in the same
    cross-process advisory lock as :mod:`claim_history`'s own append lock
    -- two overlapping sweeps must never run concurrently."""
    calls: list[Path] = []
    real_lock = claim_history_mirror.handoff_trace._append_lock

    def spy_lock(lock_path):
        calls.append(lock_path)
        return real_lock(lock_path)

    monkeypatch.setattr(claim_history_mirror.handoff_trace, "_append_lock", spy_lock)
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    claim_history_mirror.sync_pending()
    # record_event() above takes its own (different) append lock too --
    # only assert the mirror's own checkpoint lock was taken exactly once.
    assert calls.count(claim_history_mirror._state_lock_path()) == 1


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
