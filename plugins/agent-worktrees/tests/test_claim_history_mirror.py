"""Tests for :mod:`claim_history_mirror` (worktree-claims-transitive-finalization
Phase 3b's remote-mirroring item): the git-ref append-only mirror for
:mod:`claim_history`'s local ownership ledger, its stateless opt-in batched
sync sweep, and the ``claims history <ref> --remote`` read path.
"""

from __future__ import annotations

import json
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
    always succeeds, so every test below keeps working whether or not an
    event carries a durable ``project`` stamp."""

    def __contains__(self, _value: object) -> bool:
        return True


@pytest.fixture(autouse=True)
def _allow_all_worktrees(monkeypatch):
    monkeypatch.setattr(
        claim_history_mirror, "_current_project_worktree_ids",
        lambda: _AllowAllWorktrees(),
    )


def _entry(seq: int, *, ledger_id: str = "ledger-a", **overrides) -> dict:
    base = {
        "ts": "2026-10-03T12:00:00+00:00", "kind": "pr", "ref": "o/r#1",
        "worktree_id": "wt-a", "machine": "m1", "event": "claimed", "seq": seq,
        "ledger_id": ledger_id,
    }
    base.update(overrides)
    return base


# ── ClaimHistoryMirror.push / .push_batch / .fetch ───────────────────────

def test_push_then_fetch_round_trips_one_entry(settings: LeaseSettings):
    m = mirror(settings)
    entry = _entry(0, session_id="sess-1", note="opened")
    assert m.push(entry) is True
    fetched = m.fetch("pr", "o/r#1")
    assert len(fetched) == 1
    assert fetched[0]["ref"] == "o/r#1"
    assert fetched[0]["event"] == "claimed"
    assert fetched[0]["session_id"] == "sess-1"
    assert fetched[0]["note"] == "opened"
    assert fetched[0]["seq"] == 0
    assert fetched[0]["ledger_id"] == "ledger-a"


def test_push_preserves_an_empty_required_field(settings: LeaseSettings):
    """A legacy record can carry ``machine=""`` (``tracking.py``'s own
    degraded-attribution case) -- the serializer must keep it verbatim
    rather than treating an empty required field like an absent optional
    one, or the round-tripped entry fails its own required-field check."""
    m = mirror(settings)
    m.push(_entry(0, machine=""))
    fetched = m.fetch("pr", "o/r#1")
    assert len(fetched) == 1
    assert fetched[0]["machine"] == ""


def test_push_appends_rather_than_overwrites(settings: LeaseSettings):
    m = mirror(settings)
    assert m.push(_entry(0, ts="2026-10-03T12:00:00+00:00", event="claimed")) is True
    assert m.push(_entry(1, ts="2026-10-03T13:00:00+00:00", event="released")) is True
    fetched = m.fetch("pr", "o/r#1")
    assert [e["event"] for e in fetched] == ["claimed", "released"]


def test_push_is_a_noop_for_an_already_mirrored_identity(settings: LeaseSettings):
    m = mirror(settings)
    assert m.push(_entry(0)) is True
    assert m.push(_entry(0)) is False
    assert len(m.fetch("pr", "o/r#1")) == 1


def test_push_distinguishes_two_distinct_events_with_an_identical_payload(
    settings: LeaseSettings,
):
    """``record_event`` timestamps only to the second, so a claim released
    and re-claimed by the same worktree/session within one second can
    produce two otherwise byte-identical "claimed" records. Payload
    equality must never stand in for identity -- each gets its own
    ``seq`` and both must land as distinct commits."""
    m = mirror(settings)
    assert m.push(_entry(0)) is True
    assert m.push(_entry(1)) is True  # same payload apart from seq
    fetched = m.fetch("pr", "o/r#1")
    assert len(fetched) == 2
    assert [e["seq"] for e in fetched] == [0, 1]


def test_push_recognizes_an_earlier_event_even_after_a_later_one_landed(
    settings: LeaseSettings,
):
    """A push retried after a later, unrelated event already landed on top
    of it (e.g. a retry racing another writer) must recognize its OWN
    event is already present somewhere in the chain -- not just at the
    tip -- and no-op rather than duplicating it."""
    m = mirror(settings)
    e = m.push(_entry(0, event="claimed"))
    f = m.push(_entry(1, event="released"))
    assert e is True and f is True
    # A retry of the first push (as if a caller re-observed it as pending).
    assert m.push(_entry(0, event="claimed")) is False
    fetched = m.fetch("pr", "o/r#1")
    assert [e["event"] for e in fetched] == ["claimed", "released"]


def test_push_distinguishes_the_same_seq_from_two_different_ledgers(settings: LeaseSettings):
    """``seq`` alone is only unique WITHIN one ledger's lifetime -- two
    independent machines' (or a reimaged machine's fresh incarnation's)
    first events for the same PR can both legitimately be ``seq=0``.
    ``ledger_id`` must discriminate them; neither may be skipped as a
    false duplicate of the other."""
    m = mirror(settings)
    assert m.push(_entry(0, ledger_id="ledger-a", event="claimed")) is True
    assert m.push(_entry(0, ledger_id="ledger-b", event="claimed")) is True
    fetched = m.fetch("pr", "o/r#1")
    assert len(fetched) == 2
    assert {e["ledger_id"] for e in fetched} == {"ledger-a", "ledger-b"}


def test_push_batch_pushes_every_missing_entry_as_one_chain(settings: LeaseSettings):
    m = mirror(settings)
    entries = [_entry(i, event=e) for i, e in enumerate(["claimed", "released", "claimed"])]
    pushed = m.push_batch(entries)
    assert pushed == 3
    fetched = m.fetch("pr", "o/r#1")
    assert [e["event"] for e in fetched] == ["claimed", "released", "claimed"]


def test_push_batch_skips_already_mirrored_entries_within_the_batch(settings: LeaseSettings):
    m = mirror(settings)
    m.push(_entry(0, event="claimed"))
    # A batch re-submitting the already-mirrored entry 0 alongside a
    # genuinely new entry 1 must push only the new one.
    pushed = m.push_batch([_entry(0, event="claimed"), _entry(1, event="released")])
    assert pushed == 1
    fetched = m.fetch("pr", "o/r#1")
    assert [e["event"] for e in fetched] == ["claimed", "released"]


def test_push_batch_is_a_noop_when_every_entry_is_already_mirrored(settings: LeaseSettings):
    m = mirror(settings)
    m.push(_entry(0))
    assert m.push_batch([_entry(0)]) == 0


def test_push_batch_rejects_a_mixed_resource_batch(settings: LeaseSettings):
    m = mirror(settings)
    with pytest.raises(ValueError):
        m.push_batch([_entry(0, ref="o/r#1"), _entry(1, ref="o/r#2")])


def test_push_batch_empty_is_a_noop(settings: LeaseSettings):
    assert mirror(settings).push_batch([]) == 0


def test_push_batch_retries_a_transient_remote_error_during_the_snapshot_read(
    settings: LeaseSettings, monkeypatch,
):
    """``_fetch_chain`` can raise ``ClaimHistoryMirrorError`` (e.g. an
    ``ls-remote`` transport failure), not just ``_SnapshotUnavailable`` --
    both must be treated as retryable rather than escaping immediately
    and abandoning the batch."""
    m = mirror(settings)
    calls = {"n": 0}
    real_fetch_chain = m._fetch_chain

    def flaky_fetch_chain(kind, ref_value):
        calls["n"] += 1
        if calls["n"] == 1:
            raise claim_history_mirror.ClaimHistoryMirrorError("transient ls-remote failure")
        return real_fetch_chain(kind, ref_value)

    monkeypatch.setattr(m, "_fetch_chain", flaky_fetch_chain)
    assert m.push_batch([_entry(0)]) == 1
    assert calls["n"] >= 2


def test_push_batch_retries_when_remote_oid_check_fails_after_a_push_failure(
    settings: LeaseSettings, monkeypatch,
):
    """A ``_remote_oid()`` call used only to check whether a just-failed
    push actually landed (a benign race) must itself be guarded -- a
    transient failure there must be treated as "couldn't confirm, retry,"
    never let escape and abort the batch outright."""
    m = mirror(settings)
    # Something is already mirrored (bypassing m), so the push m is about
    # to attempt -- built against a snapshot that (lyingly) claims the ref
    # is absent -- genuinely fails its force-with-lease against the real
    # remote state.
    conflict = mirror(settings)
    conflict.push(_entry(99, event="claimed"))

    real_fetch_chain = m._fetch_chain
    fetch_calls = {"n": 0}

    def lying_fetch_chain(kind, ref_value):
        fetch_calls["n"] += 1
        if fetch_calls["n"] == 1:
            return None, []  # a stale/lying snapshot: claims the ref is absent
        return real_fetch_chain(kind, ref_value)

    monkeypatch.setattr(m, "_fetch_chain", lying_fetch_chain)

    real_remote_oid = m._remote_oid
    oid_calls = {"n": 0}

    def flaky_remote_oid(ref_arg):
        oid_calls["n"] += 1
        if oid_calls["n"] == 1:
            # The post-push-failure confirmation check.
            raise claim_history_mirror.ClaimHistoryMirrorError("transient ls-remote failure")
        return real_remote_oid(ref_arg)

    monkeypatch.setattr(m, "_remote_oid", flaky_remote_oid)
    assert m.push_batch([_entry(0)]) == 1
    fetched = m.fetch("pr", "o/r#1")
    assert [e["seq"] for e in fetched] == [99, 0]


def test_fetch_chain_detects_a_ref_that_moved_mid_read(settings: LeaseSettings, monkeypatch):
    """``_fetch_chain`` is the one place the remote is read; if the ref
    moves between its initial ``ls-remote`` and the follow-up fetch of
    that exact oid, it must raise rather than silently returning content
    that no longer corresponds to the oid it reported."""
    m = mirror(settings)
    m.push(_entry(0))
    real_remote_oid = m._remote_oid
    calls = {"n": 0}

    def flaky_remote_oid(ref):
        calls["n"] += 1
        if calls["n"] == 1:
            return "0" * 40  # a plausible-looking oid the follow-up fetch won't actually have
        return real_remote_oid(ref)

    monkeypatch.setattr(m, "_remote_oid", flaky_remote_oid)
    with pytest.raises(claim_history_mirror._SnapshotUnavailable):
        m._fetch_chain("pr", "o/r#1")


def test_fetch_is_empty_for_a_never_mirrored_ref(settings: LeaseSettings):
    assert mirror(settings).fetch("pr", "o/r#404") == []


def test_fetch_is_scoped_to_its_own_resource(settings: LeaseSettings):
    m = mirror(settings)
    m.push(_entry(0))
    assert mirror(settings).fetch("pr", "o/r#2") == []


def test_parse_entry_rejects_a_non_string_field():
    """A remote entry with a wrongly-typed field (e.g. ``note`` as a list)
    must be rejected at parse time -- never accepted and handed to a caller
    (``claims history --remote``'s own merge) that assumes every field is
    a plain string."""
    bad = claim_history_mirror._serialize_entry(_entry(0))
    prefix, body = bad.split("\n", 1)
    payload = json.loads(body)
    payload["note"] = ["not", "a", "string"]
    tampered = prefix + "\n" + json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ProtocolError):
        claim_history_mirror._parse_entry(tampered)


def test_parse_entry_rejects_a_non_integer_seq():
    bad = claim_history_mirror._serialize_entry(_entry(0))
    prefix, body = bad.split("\n", 1)
    payload = json.loads(body)
    payload["seq"] = "0"
    tampered = prefix + "\n" + json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ProtocolError):
        claim_history_mirror._parse_entry(tampered)


def test_parse_entry_rejects_an_entry_for_a_different_resource():
    """A well-formed entry whose own ``(kind, ref)`` names a DIFFERENT
    resource than the one it was fetched under must never be accepted --
    it would appear in the wrong resource's history, and a coincidentally
    matching identity could suppress a genuinely pending event."""
    message = claim_history_mirror._serialize_entry(_entry(0, ref="o/r#1"))
    with pytest.raises(ProtocolError):
        claim_history_mirror._parse_entry(message, expected=("pr", "o/r#2"))
    # The correct expectation still accepts it.
    claim_history_mirror._parse_entry(message, expected=("pr", "o/r#1"))


# ── _ledger_id ────────────────────────────────────────────────────────────

def test_ledger_id_is_stable_across_calls():
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    first = claim_history_mirror._ledger_id()
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="released",
    )
    second = claim_history_mirror._ledger_id()
    assert first == second


def test_ledger_id_changes_after_the_ledger_file_is_removed(monkeypatch):
    """A reimaged machine starting a fresh, empty ledger must mint a fresh
    incarnation id -- never silently reuse a stale one left over from a
    stray sidecar file."""
    first = claim_history_mirror._ledger_id()
    claim_history.history_path().unlink(missing_ok=True)
    second = claim_history_mirror._ledger_id()
    assert first != second


def test_record_event_rotates_a_stale_ledger_id_when_recreating_the_file(
    settings: LeaseSettings, monkeypatch,
):
    """If the ledger is deleted after a sync and ``record_event()``
    recreates it before the next sweep, the NEW event must get a FRESH
    ledger identity -- reusing the old sidecar would restart this
    resource's own seq numbering at 0, colliding with whatever the old
    identity already mirrored and silently suppressing the new event."""
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    claim_history_mirror.sync_pending()
    old_mirrored = mirror(settings).fetch("pr", "o/r#1")
    assert len(old_mirrored) == 1

    # The ledger is lost (a reimage, a bad cleanup, ...) -- its sidecar is
    # NOT independently deleted, simulating the exact stray-sidecar
    # scenario.
    claim_history.history_path().unlink(missing_ok=True)
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="released",
    )

    new_mirrored_count = claim_history_mirror.sync_pending()["pushed"]
    assert new_mirrored_count == 1  # the new event is NOT suppressed as a false duplicate
    fetched = mirror(settings).fetch("pr", "o/r#1")
    assert [e["event"] for e in fetched] == ["claimed", "released"]
    assert fetched[0]["ledger_id"] != fetched[1]["ledger_id"]


def test_ledger_id_serializes_initialization(monkeypatch):
    """Two overlapping first-time callers racing to initialize the ledger
    id sidecar must never each mint and persist a DIFFERENT id -- the
    whole check-then-mint-then-persist sequence is one locked critical
    section, and it must be the SAME lock record_event's own ledger
    append path uses (so a concurrent rotate-on-recreate can never
    interleave with a mint-or-reuse read)."""
    calls: list = []
    real_lock = claim_history_mirror.handoff_trace._append_lock

    def spy_lock(lock_path):
        calls.append(lock_path)
        return real_lock(lock_path)

    monkeypatch.setattr(claim_history_mirror.handoff_trace, "_append_lock", spy_lock)
    claim_history_mirror._ledger_id()
    assert len(calls) == 1
    ledger_path = claim_history.history_path()
    assert calls[0] == ledger_path.with_suffix(ledger_path.suffix + ".lock")


def test_rotate_ledger_id_sidecar_falls_back_to_truncating_when_unlink_fails(monkeypatch):
    """If outright deletion of the stale sidecar fails (not merely
    "already absent"), the rotation must still make the content
    untrustworthy (empty) rather than leaving a fully-valid-looking
    stale id in place -- a silent deletion failure must never let an old
    incarnation survive a recreate."""
    old_id = claim_history_mirror._ledger_id()
    id_path = claim_history_mirror._ledger_id_path()
    assert id_path.read_text(encoding="utf-8").strip() == old_id

    real_unlink = type(id_path).unlink

    def failing_unlink(self, *a, **k):
        if self == id_path:
            raise OSError("permission denied")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(type(id_path), "unlink", failing_unlink)
    claim_history._rotate_ledger_id_sidecar()
    assert id_path.read_text(encoding="utf-8").strip() == ""

    monkeypatch.undo()
    new_id = claim_history_mirror._ledger_id()
    assert new_id != old_id


def test_ledger_id_raises_on_a_sidecar_read_failure_rather_than_replacing_it(monkeypatch):
    """A transient read failure on an EXISTING, valid sidecar must never
    fall through to minting a replacement -- the old id may still be
    perfectly correct, and a spurious rotation would make every event it
    already mirrored look brand new again."""
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    original_id = claim_history_mirror._ledger_id()
    id_path = claim_history_mirror._ledger_id_path()
    real_read_text = type(id_path).read_text

    def failing_read_text(self, *a, **k):
        if self == id_path:
            raise OSError("transient I/O error")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(type(id_path), "read_text", failing_read_text)
    with pytest.raises(OSError):
        claim_history_mirror._ledger_id()
    monkeypatch.undo()
    # The sidecar's own real content survives untouched.
    assert id_path.read_text(encoding="utf-8").strip() == original_id


def test_grouped_events_reads_identity_and_content_under_one_lock_acquisition(
    monkeypatch,
):
    """A SEPARATE id-read and content-read (two lock acquisitions) leaves
    a window where ``record_event()`` can recreate the ledger (rotating
    its identity) in between, stamping stale content with a fresh id or
    vice versa. One acquisition for both closes that window."""
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    calls: list = []
    real_lock = claim_history_mirror.handoff_trace._append_lock

    def spy_lock(lock_path):
        calls.append(lock_path)
        return real_lock(lock_path)

    monkeypatch.setattr(claim_history_mirror.handoff_trace, "_append_lock", spy_lock)
    claim_history_mirror._grouped_events()
    assert len(calls) == 1


def test_local_identities_for_ref_reads_identity_and_content_under_one_lock(
    monkeypatch,
):
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    calls: list = []
    real_lock = claim_history_mirror.handoff_trace._append_lock

    def spy_lock(lock_path):
        calls.append(lock_path)
        return real_lock(lock_path)

    monkeypatch.setattr(claim_history_mirror.handoff_trace, "_append_lock", spy_lock)
    claim_history_mirror.local_identities_for_ref("pr", "o/r#1")
    assert len(calls) == 1


def test_ledger_id_readonly_is_none_before_any_initialization():
    assert claim_history_mirror._ledger_id_readonly() is None


def test_ledger_id_readonly_never_mints(monkeypatch):
    """A pure display/read path must never have the side effect of
    creating a fresh ledger identity merely by being invoked."""
    assert claim_history_mirror._ledger_id_readonly() is None
    assert not claim_history_mirror._ledger_id_path().exists()


def test_ledger_id_readonly_returns_the_persisted_id_once_minted():
    minted = claim_history_mirror._ledger_id()
    assert claim_history_mirror._ledger_id_readonly() == minted


# ── local_identities_for_ref ──────────────────────────────────────────────

def test_local_identities_for_ref_is_none_ledger_id_before_any_sync():
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    stamped = claim_history_mirror.local_identities_for_ref("pr", "o/r#1")
    assert len(stamped) == 1
    assert stamped[0]["ledger_id"] is None
    assert stamped[0]["seq"] == 0
    # A read-only display call must never mint the sidecar.
    assert not claim_history_mirror._ledger_id_path().exists()


def test_local_identities_for_ref_matches_a_real_sync_sweeps_own_stamping(
    settings: LeaseSettings, monkeypatch,
):
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    claim_history_mirror.sync_pending()  # mints + uses the real ledger id
    stamped = claim_history_mirror.local_identities_for_ref("pr", "o/r#1")
    remote = mirror(settings).fetch("pr", "o/r#1")
    assert stamped[0]["ledger_id"] == remote[0]["ledger_id"]
    assert stamped[0]["seq"] == remote[0]["seq"]


# ── _grouped_events ───────────────────────────────────────────────────────

def test_grouped_events_stamps_a_stable_position_based_seq_and_ledger_id():
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="released",
    )
    claim_history.record_event(
        kind="pr", ref="o/r#2", worktree_id="wt-b", machine="m1", event="claimed",
    )
    grouped = claim_history_mirror._grouped_events()
    assert [e["seq"] for e in grouped[("pr", "o/r#1")]] == [0, 1]
    assert [e["seq"] for e in grouped[("pr", "o/r#2")]] == [0]
    ids = {e["ledger_id"] for events in grouped.values() for e in events}
    assert len(ids) == 1  # the same local ledger incarnation for every entry


# ── _event_eligible / durable project attribution ────────────────────────

def test_event_eligible_prefers_a_durable_project_stamp_over_the_heuristic():
    stamped = {"project": "proj-a", "worktree_id": "wt-gone"}
    assert claim_history_mirror._event_eligible(
        stamped, project_name="proj-a", owned_ids=set()
    ) is True
    assert claim_history_mirror._event_eligible(
        stamped, project_name="proj-b", owned_ids={"wt-gone"}
    ) is False  # the durable stamp disagrees with the live heuristic -- it wins


def test_event_eligible_falls_back_to_the_heuristic_for_a_legacy_unstamped_event():
    legacy = {"worktree_id": "wt-a"}
    assert claim_history_mirror._event_eligible(
        legacy, project_name="proj-a", owned_ids={"wt-a"}
    ) is True
    assert claim_history_mirror._event_eligible(
        legacy, project_name="proj-a", owned_ids=set()
    ) is False


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


def test_sync_pending_dry_run_reports_nothing_once_already_mirrored(
    settings: LeaseSettings, monkeypatch
):
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )
    claim_history_mirror.sync_pending()
    result = claim_history_mirror.sync_pending(dry_run=True)
    assert result["refs"] == []


def test_sync_pending_dry_run_reports_an_unreachable_store_as_failed_not_zero(
    settings: LeaseSettings, monkeypatch
):
    """An unreachable store raises ``ClaimHistoryMirrorError`` from
    ``_remote_oid``'s own checked ``ls-remote`` -- a NARROWER except
    clause catching only ``_SnapshotUnavailable`` would let this escape
    and abort the whole dry-run sweep instead of reporting the one
    affected ref and continuing."""
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )

    def boom(self, ref):
        raise claim_history_mirror.ClaimHistoryMirrorError("store unreachable")

    monkeypatch.setattr(claim_history_mirror.ClaimHistoryMirror, "_remote_oid", boom)
    result = claim_history_mirror.sync_pending(dry_run=True)
    assert result["refs"] == []
    assert len(result["failed"]) == 1
    assert result["failed"][0]["ref"] == "o/r#1"


def test_sync_pending_reports_an_unreadable_ledger_rather_than_a_clean_sweep(
    settings: LeaseSettings, monkeypatch
):
    """An ``OSError`` reading an EXISTING ledger must be reported as a
    genuine read failure -- never silently degrade to an empty grouping,
    which would make ``sync_pending()`` indistinguishable from a
    genuinely clean, fully-synced sweep (``available=True, pushed=0,
    failed=[]``)."""
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
    )

    import builtins
    real_open = builtins.open

    def flaky_open(path, *a, **k):
        if str(path) == str(claim_history.history_path()):
            raise OSError("permission denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", flaky_open)
    result = claim_history_mirror.sync_pending()
    assert result["pushed"] == 0
    assert len(result["failed"]) == 1
    assert "unreadable" in result["failed"][0]["error"]



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
    own tracking records don't recognize must never ride along. Forces the
    legacy tracking-record heuristic (no durable project stamp) so this
    test exercises that fallback path specifically."""
    monkeypatch.setattr(claim_history, "current_project_name", lambda: None)
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


def test_sync_pending_survives_a_reaped_tracking_record_via_the_durable_stamp(
    settings: LeaseSettings, monkeypatch
):
    """A legacy, tracking-record-based eligibility check alone would
    permanently drop an event the instant its worktree is reaped. A
    durable ``project`` stamp recorded at write time must keep that event
    eligible regardless."""
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    # No worktree is "owned" by the live heuristic at all (simulating full
    # reap), but the event itself carries a durable project stamp.
    monkeypatch.setattr(claim_history_mirror, "_current_project_worktree_ids", set)
    monkeypatch.setattr(claim_history, "current_project_name", lambda: "proj-a")

    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-reaped", machine="m1", event="claimed",
    )
    result = claim_history_mirror.sync_pending()
    assert result["pushed"] == 1
    assert mirror(settings).fetch("pr", "o/r#1")[0]["event"] == "claimed"


def test_sync_pending_propagates_an_explicit_project_over_ambient_config(
    settings: LeaseSettings, monkeypatch
):
    """``record_event``'s explicit ``project=`` (the OWNING record's own
    ``repo``) must win even when ambient config disagrees -- the exact
    cross-project ``--owner-ref``/daemon-dispatch mismatch a prior review
    round flagged."""
    monkeypatch.setattr(claim_history, "current_project_name", lambda: "ambient-project")
    monkeypatch.setattr(
        claim_history_mirror, "mirror_settings", lambda origin=None: settings
    )
    claim_history.record_event(
        kind="pr", ref="o/r#1", worktree_id="wt-a", machine="m1", event="claimed",
        project="actual-owning-project",
    )
    # The sweep's own project context matches the explicit stamp, not the
    # (deliberately different) ambient one.
    monkeypatch.setattr(claim_history, "current_project_name", lambda: "actual-owning-project")
    result = claim_history_mirror.sync_pending()
    assert result["pushed"] == 1


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


# ── cleanup_gc_cli._run_claim_history_mirror (the "never fail gc" contract) ──

def test_run_claim_history_mirror_never_fails_gc_on_an_unexpected_exception(monkeypatch):
    """``gc --mirror-claim-history`` must never fail the rest of ``gc``,
    even for a failure ``sync_pending()`` didn't anticipate itself (an
    import-time error, a non-``ConfigError`` settings-resolution failure,
    ...) -- not just the ones it already catches internally."""
    import argparse

    from agent_worktrees import cleanup_gc_cli

    def boom(**_kwargs):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(claim_history_mirror, "sync_pending", boom)
    result = cleanup_gc_cli._run_claim_history_mirror(argparse.Namespace(dry_run=False))
    assert result["available"] is True
    assert result["pushed"] == 0
    assert len(result["failed"]) == 1
    assert "unexpected failure" in result["failed"][0]["error"]
    # Must not raise -- the print helper handles it like any other failure.
    cleanup_gc_cli._print_gc_claim_history_mirror(result, dry=False)

