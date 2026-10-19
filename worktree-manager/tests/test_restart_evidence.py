"""Restart evidence is durable metadata, never independent launch authority."""

from __future__ import annotations

import dataclasses
import importlib
import json
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest


NATIVE_ID = "00000000-0000-4000-8000-000000000001"
OTHER_NATIVE_ID = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def recovery():
    return importlib.import_module("worktree_manager.restart_evidence")


def positive(recovery, **changes):
    values = {
        "project": "example",
        "worktree_id": "worktree-1",
        "worktree_path": "/path/to/worktree-1",
        "native_session_id": NATIVE_ID,
        "native_incarnation": "native:original",
        "binding_proof": "inuse-hold-v1",
        "owner_revision": 1,
        "session_incarnation": "mux:1:100",
        "mapping_revision": 3,
        "platform": "linux",
        "environment_id": "example-environment",
        "boot_id": "boot-one",
        "observed_at": 100.0,
        "observation_sequence": 1,
    }
    values.update(changes)
    return recovery.PositiveEvidence(**values)


def test_positive_survives_independent_reader_and_reasonless_loss(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    evidence = positive(recovery)
    assert journal.observe(evidence)["applied"]
    assert journal.record_exit(evidence, reason="unknown")["applied"]
    row = recovery.RecoveryJournal(tmp_path).snapshot()[0]
    assert row["last_positive"] == dataclasses.asdict(evidence)
    assert row["disposition"] == "unknown"
    assert row["automatic_eligible"] is False


def test_consumed_end_is_monotonic_until_execution_generation_changes(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    evidence = positive(recovery)
    journal.observe(evidence)
    journal.record_exit(evidence, reason="unknown", end_consumed=True)
    journal.record_exit(evidence, reason="unknown")
    assert journal.snapshot()[0]["end_consumed"] is True
    journal.observe(dataclasses.replace(evidence, observation_sequence=2, observed_at=110.0))
    assert journal.snapshot()[0]["end_consumed"] is True
    assert journal.snapshot()[0]["disposition"] == "unknown"
    replacement = dataclasses.replace(
        evidence, native_incarnation="native:replacement", owner_revision=2,
        observation_sequence=3, observed_at=120.0,
    )
    assert journal.observe(replacement)["applied"]
    assert journal.snapshot()[0]["end_consumed"] is False
    assert journal.record_exit(
        evidence, reason="unknown", end_consumed=True,
    ) == {"applied": False, "reason": "superseded"}
    assert journal.snapshot()[0]["end_consumed"] is False


@pytest.mark.parametrize("cancelled", [False, True])
def test_consumed_end_preserves_shutdown_uncertainty(tmp_path, recovery, cancelled):
    journal = recovery.RecoveryJournal(tmp_path)
    evidence = positive(recovery)
    journal.observe(evidence)
    journal.shutdown_pending(
        "linux", "example-environment", "boot-one", notification_id="one",
    )
    if cancelled:
        journal.shutdown_cancelled(
            "linux", "example-environment", "boot-one", notification_id="one",
        )
    journal.record_exit(evidence, reason="unknown", end_consumed=True)
    row = journal.snapshot()[0]
    assert row["end_consumed"] is True
    assert row["disposition"] == ("unknown" if cancelled else "shutdown-interrupted")
    assert row["notification_id"] == ("" if cancelled else "one")
    if cancelled:
        assert row["detail"] == "shutdown-cancelled"


def test_legacy_end_consumption_defaults_false_and_rejects_unknown_metadata(recovery):
    row = {
        "last_positive": dataclasses.asdict(positive(recovery)),
        "disposition": "unknown", "detail": "", "notification_id": "",
    }
    assert recovery._decode(json.dumps(row))["end_consumed"] is False
    assert recovery._decode(json.dumps({**row, "end_consumed": True}))["end_consumed"] is True
    for changes in (
        {"end_consumed": 1}, {"end_consumed": None}, {"end_consumed": "true"},
        {"other": False}, {"end_consumed": False, "other": False},
    ):
        with pytest.raises(ValueError):
            recovery._decode(json.dumps({**row, **changes}))


def test_explicit_close_is_durable_and_periodic_refresh_cannot_reopen(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    evidence = positive(recovery)
    journal.observe(evidence)
    journal.record_exit(evidence, reason="intentional", detail="operator-stop")
    assert journal.observe(dataclasses.replace(
        evidence, observation_sequence=2, observed_at=110.0,
    )) == {"applied": False, "reason": "closed"}
    assert recovery.RecoveryJournal(tmp_path).snapshot()[0]["disposition"] == "intentional"


def test_activation_identity_not_capability_controls_generation(recovery):
    first = positive(recovery, activation_ordinal=1, source_identity="native:source")
    qualified = dataclasses.replace(first, lifecycle_qualified=True)
    assert first.identity() == qualified.identity()
    replacement = dataclasses.replace(qualified, activation_ordinal=2)
    assert replacement.identity() != first.identity()
    assert replacement.provider_identity() == first.provider_identity()


def test_legacy_positive_has_no_invented_activation_proof(recovery):
    evidence = positive(recovery)
    assert evidence.activation_ordinal == 0
    assert evidence.source_identity is None
    assert evidence.lifecycle_qualified is False


@pytest.mark.parametrize("changes", [
    {"activation_ordinal": True}, {"activation_ordinal": -1},
    {"source_identity": "native:source"}, {"activation_ordinal": 1},
    {"lifecycle_qualified": True}, {"lifecycle_qualified": "true"},
])
def test_invalid_activation_proof_is_rejected(recovery, changes):
    with pytest.raises(ValueError):
        positive(recovery, **changes)


def test_cancelled_shutdown_keeps_delayed_native_exit_uncertain(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    evidence = positive(recovery)
    journal.observe(evidence)
    journal.shutdown_pending(
        "linux", "example-environment", "boot-one", notification_id="one",
    )
    journal.shutdown_cancelled(
        "linux", "example-environment", "boot-one", notification_id="one",
    )
    journal.observe(dataclasses.replace(evidence, observation_sequence=2, observed_at=110.0))
    for _ in range(2):
        assert journal.record_exit(
            evidence, reason="intentional", detail="native-session-end",
        )["applied"]
        row = journal.snapshot()[0]
        assert row["disposition"] == "unknown"
        assert row["detail"] == "native-end-after-cancel-unconfirmed"


def test_positive_corroboration_clears_unknown_not_shutdown(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    evidence = positive(recovery)
    journal.observe(evidence)
    journal.record_exit(evidence, reason="unknown")
    journal.observe(dataclasses.replace(evidence, observation_sequence=2, observed_at=110.0))
    assert journal.snapshot()[0]["disposition"] == "live"
    journal.shutdown_pending("linux", "example-environment", "boot-one", notification_id="one")
    journal.observe(dataclasses.replace(evidence, observation_sequence=3, observed_at=120.0))
    assert journal.snapshot()[0]["disposition"] == "shutdown-pending"


@pytest.mark.parametrize("native_first", [False, True])
def test_unknown_exit_preserves_cancelled_shutdown_uncertainty(tmp_path, recovery, native_first):
    journal = recovery.RecoveryJournal(tmp_path)
    evidence = positive(recovery)
    journal.observe(evidence)
    journal.shutdown_pending(
        "linux", "example-environment", "boot-one", notification_id="one",
    )
    journal.shutdown_cancelled(
        "linux", "example-environment", "boot-one", notification_id="one",
    )
    if native_first:
        journal.record_exit(evidence, reason="intentional", detail="native-session-end")
    marker = journal.snapshot()[0]["detail"]
    for _ in range(2):
        journal.record_exit(evidence, reason="unknown", detail="native-end-cause-unconfirmed")
        assert journal.snapshot()[0]["detail"] == marker
    journal.record_exit(evidence, reason="intentional", detail="native-session-end")
    assert journal.snapshot()[0]["disposition"] == "unknown"
    journal.record_exit(evidence, reason="intentional", detail="operator-stop")
    assert journal.snapshot()[0]["disposition"] == "intentional"


@pytest.mark.parametrize("reason", ["intentional", "unknown", "shutdown-interrupted"])
def test_delayed_old_exit_cannot_retire_replacement(tmp_path, recovery, reason):
    journal = recovery.RecoveryJournal(tmp_path)
    first = positive(recovery)
    replacement = positive(
        recovery, native_session_id=OTHER_NATIVE_ID, session_incarnation="mux:2:200",
        mapping_revision=4,
    )
    journal.observe(first)
    journal.observe(replacement)
    assert journal.record_exit(first, reason=reason, detail="operator-stop") == {
        "applied": False, "reason": "superseded",
    }
    assert journal.snapshot()[0]["last_positive"] == dataclasses.asdict(replacement)
    assert journal.snapshot()[0]["disposition"] == "live"


def test_same_revision_different_identity_is_ambiguous_not_replacement(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    journal.observe(positive(recovery))
    assert journal.observe(positive(recovery, native_session_id=OTHER_NATIVE_ID)) == {
        "applied": False, "reason": "identity-conflict",
    }
    assert journal.snapshot()[0]["last_positive"]["native_session_id"] == NATIVE_ID


def test_native_restart_inside_same_mux_fences_old_exit(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    first = positive(recovery)
    journal.observe(first)
    replacement = dataclasses.replace(
        first, native_incarnation="native:replacement", owner_revision=2,
        observation_sequence=2, observed_at=110.0,
    )
    assert journal.observe(replacement)["applied"]
    assert journal.record_exit(first, reason="intentional", detail="native-session-end") == {
        "applied": False, "reason": "superseded",
    }
    assert journal.snapshot()[0]["last_positive"]["native_incarnation"] == "native:replacement"


def test_old_native_generation_cannot_overwrite_same_mux_replacement(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    first = positive(recovery)
    journal.observe(first)
    replacement = dataclasses.replace(
        first, native_incarnation="native:replacement", owner_revision=2,
        observation_sequence=2, observed_at=110.0,
    )
    journal.observe(replacement)
    delayed = dataclasses.replace(first, observation_sequence=3, observed_at=120.0)
    assert journal.observe(delayed) == {"applied": False, "reason": "stale"}
    assert journal.snapshot()[0]["last_positive"]["native_incarnation"] == "native:replacement"


def test_same_mux_head_succession_uses_owner_revision(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    first = positive(recovery)
    journal.observe(first)
    successor = dataclasses.replace(
        first, native_session_id=OTHER_NATIVE_ID, native_incarnation="native:successor",
        owner_revision=2, observation_sequence=2, observed_at=110.0,
    )
    assert journal.observe(successor)["applied"]
    assert journal.snapshot()[0]["last_positive"]["native_session_id"] == OTHER_NATIVE_ID
    assert journal.record_exit(first, reason="intentional", detail="native-session-end") == {
        "applied": False, "reason": "superseded",
    }


def test_mapping_refresh_cannot_reopen_an_intentionally_closed_execution(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    first = positive(recovery)
    journal.observe(first)
    journal.record_exit(first, reason="intentional", detail="operator-stop")
    refreshed = dataclasses.replace(
        first, mapping_revision=4, observation_sequence=2, observed_at=110.0,
    )
    assert journal.observe(refreshed) == {"applied": False, "reason": "closed"}
    assert journal.snapshot()[0]["disposition"] == "intentional"


def test_mapping_refresh_preserves_shutdown_and_accepts_same_execution_exit(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    first = positive(recovery)
    journal.observe(first)
    journal.shutdown_pending("linux", "example-environment", "boot-one", notification_id="one")
    refreshed = dataclasses.replace(
        first, mapping_revision=4, observation_sequence=2, observed_at=110.0,
    )
    assert journal.observe(refreshed)["applied"]
    assert journal.snapshot()[0]["disposition"] == "shutdown-pending"
    assert journal.record_exit(first, reason="unknown")["applied"]
    assert journal.snapshot()[0]["disposition"] == "shutdown-interrupted"


def test_repeated_unknown_preserves_shutdown_interrupted_through_positive(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    first = positive(recovery)
    journal.observe(first)
    journal.shutdown_pending("linux", "example-environment", "boot-one", notification_id="one")
    journal.record_exit(first, reason="unknown")
    journal.record_exit(first, reason="unknown")
    assert journal.snapshot()[0]["disposition"] == "shutdown-interrupted"
    assert journal.observe(dataclasses.replace(
        first, observation_sequence=2, observed_at=110.0,
    ))["applied"]
    assert journal.snapshot()[0]["disposition"] == "shutdown-interrupted"


def test_shutdown_cancel_restores_only_the_frozen_generation(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    evidence = positive(recovery)
    journal.observe(evidence)
    journal.shutdown_pending("linux", "example-environment", "boot-one", notification_id="one")
    assert journal.snapshot()[0]["disposition"] == "shutdown-pending"
    journal.shutdown_cancelled(
        "linux", "example-environment", "boot-one", notification_id="different",
    )
    assert journal.snapshot()[0]["disposition"] == "shutdown-pending"
    journal.shutdown_cancelled(
        "linux", "example-environment", "boot-one", notification_id="one",
    )
    assert journal.snapshot()[0]["disposition"] == "live"
    assert journal.snapshot()[0]["last_positive"] == dataclasses.asdict(evidence)


def test_shutdown_exit_is_not_intentional_and_retains_positive(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    evidence = positive(recovery)
    journal.observe(evidence)
    journal.shutdown_pending("linux", "example-environment", "boot-one", notification_id="one")
    journal.record_exit(evidence, reason="unknown")
    row = journal.snapshot()[0]
    assert row["disposition"] == "shutdown-interrupted"
    assert row["last_positive"] == dataclasses.asdict(evidence)
    assert row["automatic_eligible"] is False


def test_explicit_close_during_shutdown_is_still_closed(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    evidence = positive(recovery)
    journal.observe(evidence)
    journal.shutdown_pending("linux", "example-environment", "boot-one", notification_id="one")
    journal.record_exit(evidence, reason="intentional", detail="operator-stop")
    journal.shutdown_cancelled(
        "linux", "example-environment", "boot-one", notification_id="one",
    )
    assert journal.snapshot()[0]["disposition"] == "intentional"


def test_stale_positive_cannot_overwrite_fresh_observation(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    journal.observe(positive(recovery, observation_sequence=2, observed_at=110.0))
    assert journal.observe(positive(recovery)) == {"applied": False, "reason": "stale"}
    assert journal.snapshot()[0]["last_positive"]["observed_at"] == 110.0


@pytest.mark.parametrize("changes", [
    {"native_session_id": "not-a-native-uuid"},
    {"session_incarnation": ""},
    {"native_incarnation": ""},
    {"binding_proof": None},
    {"binding_proof": "pid-name-only"},
    {"owner_revision": True},
    {"owner_revision": -1},
    {"boot_id": ""},
    {"environment_id": ""},
    {"platform": "unknown"},
    {"observation_sequence": True},
    {"observation_sequence": 0},
    {"observed_at": float("nan")},
    {"mapping_revision": -1},
])
def test_incomplete_evidence_is_rejected_before_write(tmp_path, recovery, changes):
    with pytest.raises(ValueError):
        recovery.RecoveryJournal(tmp_path).observe(positive(recovery, **changes))
    assert not (tmp_path / "restart-recovery").exists()


def test_reason_code_alone_cannot_assert_intentional_close(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    evidence = positive(recovery)
    journal.observe(evidence)
    with pytest.raises(ValueError, match="explicit"):
        journal.record_exit(evidence, reason="intentional")
    assert journal.snapshot()[0]["disposition"] == "live"


def test_absent_read_is_read_only_and_corruption_is_visible(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    assert journal.snapshot() == []
    assert not journal.path.exists()
    journal.path.parent.mkdir()
    journal.path.write_bytes(b"not a SQLite database")
    with pytest.raises(recovery.RecoveryEvidenceError):
        journal.snapshot()
    assert journal.path.read_bytes() == b"not a SQLite database"


def test_unrelated_sqlite_schema_is_not_reinitialized(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    journal.path.parent.mkdir()
    with sqlite3.connect(journal.path) as conn:
        conn.execute("CREATE TABLE unrelated(value TEXT)")
    before = journal.path.read_bytes()
    with pytest.raises(recovery.RecoveryEvidenceError):
        journal.snapshot(recover_existing=True)
    assert journal.path.read_bytes() == before
    with pytest.raises(recovery.RecoveryEvidenceError):
        journal.observe(positive(recovery))
    with sqlite3.connect(journal.path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == [("unrelated",)]


def test_authorized_storage_recovery_never_creates_missing_database(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    assert journal.snapshot(recover_existing=True) == []
    assert not journal.path.parent.exists()


def test_symlink_storage_is_refused_without_touching_target(tmp_path, recovery):
    target = tmp_path / "target"
    target.mkdir()
    try:
        (tmp_path / "restart-recovery").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Native fixture cannot create a symbolic link")
    with pytest.raises(recovery.RecoveryEvidenceError):
        recovery.RecoveryJournal(tmp_path).observe(positive(recovery))
    assert list(target.iterdir()) == []


def test_private_storage_and_bounded_metadata(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    journal.observe(positive(recovery))
    if __import__("os").name != "nt":
        assert stat.S_IMODE(journal.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(journal.path.parent.stat().st_mode) == 0o700
    encoded = json.dumps(journal.snapshot())
    assert "prompt" not in encoded and "transcript" not in encoded
    with pytest.raises(ValueError):
        journal.observe(positive(recovery, project="x" * 4097))


def test_concurrent_writers_keep_highest_sequence(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    journal.observe(positive(recovery))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(
            lambda sequence: recovery.RecoveryJournal(tmp_path).observe(
                positive(recovery, observation_sequence=sequence, observed_at=100.0 + sequence),
            ),
            range(2, 18),
        ))
    assert journal.snapshot()[0]["last_positive"]["observation_sequence"] == 17


def test_write_denial_is_visible_and_does_not_rewrite_evidence(tmp_path, recovery, monkeypatch):
    journal = recovery.RecoveryJournal(tmp_path)
    journal.observe(positive(recovery))
    before = journal.path.read_bytes()

    def denied(*_args, **_kwargs):
        raise sqlite3.OperationalError("read-only database")

    monkeypatch.setattr(recovery.sqlite3, "connect", denied)
    with pytest.raises(recovery.RecoveryEvidenceError):
        journal.observe(positive(recovery, observation_sequence=2))
    assert journal.path.read_bytes() == before


def test_journal_history_is_bounded_without_losing_last_positive(tmp_path, recovery):
    journal = recovery.RecoveryJournal(tmp_path)
    for sequence in range(1, 25):
        journal.observe(positive(
            recovery, observation_sequence=sequence, observed_at=100.0 + sequence,
        ))
    journal.record_exit(positive(recovery), reason="intentional", detail="operator-stop")
    with sqlite3.connect(journal.path) as conn:
        assert conn.execute("SELECT count(*) FROM journal").fetchone()[0] == 16
    assert journal.snapshot()[0]["last_positive"]["observation_sequence"] == 24
