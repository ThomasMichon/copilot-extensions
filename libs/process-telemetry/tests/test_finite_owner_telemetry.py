"""Registered finite-owner schemas, the hook journal and a real receiver readback."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from process_telemetry import SinkSlot, hook_journal
from process_telemetry.command import SOURCES
from process_telemetry.emission import LEGACY
from process_telemetry.finite_owner import FiniteOwner, capability, load_policy
from process_telemetry.stage_projection import for_schema

_RECEIVER = """\
import json, os, sys
from pathlib import Path
with Path("readback.jsonl").open("a", encoding="utf-8") as output:
    for line in sys.stdin:
        output.write(json.dumps({"record": json.loads(line), "cwd": os.getcwd()}) + "\\n")
"""


def _receiver(root: Path) -> Path:
    root.mkdir()
    (root / "receiver.py").write_text(_RECEIVER, encoding="utf-8")
    (root / ".agent-telemetry").mkdir()
    (root / ".agent-telemetry" / "config.json").write_text(json.dumps({
        "schema_version": 1, "mode": "command",
        "command": {"argv": [sys.executable, "-I", "receiver.py"]},
    }), encoding="utf-8")
    return root


def _readback(root: Path) -> list[dict[str, Any]]:
    path = root / "readback.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_registry_pairs_each_owner_with_one_schema() -> None:
    assert SOURCES["agent-bridge"] == ("bridge.lifecycle.v1",)
    assert SOURCES["agent-dispatch"] == ("dispatch.lifecycle.v1",)
    owner = SinkSlot().configure_commands(source="agent-worktrees", source_schema="hook.lifecycle.v1")
    assert (owner.source, owner.source_schema) == ("agent-worktrees", "hook.lifecycle.v1")
    assert SinkSlot().configure_commands(source="agent-codespaces").source_schema == (
        "codespaces.lifecycle.v1"
    )
    with pytest.raises(ValueError):
        SinkSlot().configure_commands(source="agent-codespaces", source_schema="hook.lifecycle.v1")
    with pytest.raises(ValueError):
        SinkSlot().configure_commands(source="agent-unknown")


@pytest.mark.parametrize("event, expected", [
    (
        {"kind": "stage", "stage": "seed_validation", "outcome": "failure",
         "reason": "seed_too_long", "duration_ms": 3, "attempt": 1,
         "seed": "secret prompt", "name": "example-codespace"},
        {"kind": "stage", "stage": "seed_validation", "outcome": "failure",
         "reason": "seed_too_long", "duration_ms": 3, "attempt": 1},
    ),
    (
        {"kind": "stage", "stage": "ssh", "outcome": "retry", "reason": "transport_retry",
         "attempt": 3, "error": "connection reset by host.example"},
        {"kind": "stage", "stage": "ssh", "outcome": "retry", "reason": "transport_retry",
         "attempt": 3},
    ),
    (
        {"kind": "stage", "stage": "account_resolution", "outcome": "timeout",
         "reason": "snapshot_timeout", "duration_ms": 5001, "limit_ms": 5000},
        {"kind": "stage", "stage": "account_resolution", "outcome": "timeout",
         "reason": "snapshot_timeout", "duration_ms": 5001, "limit_ms": 5000},
    ),
    (
        {"kind": "stage", "stage": "claim", "outcome": "success", "reason": "force_claimed"},
        {"kind": "stage", "stage": "claim", "outcome": "success", "reason": "force_claimed"},
    ),
])
def test_codespaces_stage_projection_admits_closed_values_only(
    event: dict[str, Any], expected: dict[str, Any],
) -> None:
    offered, project = for_schema("codespaces.lifecycle.v1")
    assert project(offered(event)) == expected


@pytest.mark.parametrize("schema, event", [
    ("codespaces.lifecycle.v1", {"kind": "stage", "stage": "exec", "outcome": "failure",
                                 "reason": "other"}),
    ("containers.lifecycle.v1", {"kind": "stage", "stage": "ssh", "outcome": "failure",
                                 "reason": "other"}),
    ("worktrees.lifecycle.v1", {"kind": "stage", "stage": "claim", "outcome": "failure",
                                "reason": "seed_too_long"}),
    ("worktrees.lifecycle.v1", {"kind": "stage", "stage": "claim", "outcome": "success",
                                "reason": "claim_busy"}),
    ("worktrees.lifecycle.v1", {"kind": "stage", "stage": "claim", "outcome": "failure",
                                "reason": "none"}),
    ("worktrees.lifecycle.v1", {"kind": "stage", "stage": "claim", "outcome": "failure",
                                "reason": "claim_busy", "attempt": 0}),
    ("worktrees.lifecycle.v1", {"kind": "stage", "stage": "claim", "outcome": "failure",
                                "reason": "claim_busy", "duration_ms": True}),
    ("worktrees.lifecycle.v1", {"kind": "stage", "stage": "claim", "outcome": "failure",
                                "reason": "force_claimed"}),
    ("codespaces.lifecycle.v1", {"kind": "stage", "stage": "ssh", "outcome": "success",
                                 "reason": "force_claimed"}),
    ("worktrees.lifecycle.v1", {"kind": "pr", "action": "rebase", "outcome": "success",
                                "reason": "none"}),
    ("worktrees.lifecycle.v1", {"kind": "pr", "action": "label", "outcome": "success",
                                "reason": "none", "label_result": "https://example.test"}),
    ("codespaces.lifecycle.v1", {"kind": "pr", "action": "create", "outcome": "success",
                                 "reason": "none"}),
    ("hook.lifecycle.v1", {"kind": "hook", "plugin": "other-plugin", "entry": "sessionStart",
                           "outcome": "success", "failure_category": "none"}),
    ("hook.lifecycle.v1", {"kind": "hook", "plugin": "agent-vault", "entry": "sessionStart",
                           "outcome": "unfinished", "failure_category": "timeout"}),
    ("hook.lifecycle.v1", {"kind": "hook", "plugin": "agent-vault", "entry": "sessionStart",
                           "outcome": "success", "failure_category": "none",
                           "started_at": "yesterday"}),
])
def test_unadmitted_records_drop(schema: str, event: dict[str, Any]) -> None:
    offered, project = for_schema(schema)
    with pytest.raises(ValueError):
        project(offered(event))


def test_pr_activity_projection_has_no_identity_fields() -> None:
    offered, project = for_schema("worktrees.lifecycle.v1")
    event = {
        "kind": "pr", "action": "label", "outcome": "success", "reason": "none",
        "label_result": "applied", "duration_ms": 12, "number": 42,
        "repo": "example/alpha", "url": "https://example.test/pull/42",
    }
    assert project(offered(event)) == {
        "kind": "pr", "action": "label", "outcome": "success", "reason": "none",
        "label_result": "applied", "duration_ms": 12,
    }


def test_hook_journal_reports_an_earlier_run_that_never_finished(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    first = hook_journal.HookRun.begin(journal, "agent-worktrees", "sessionStart")
    assert first.marker is not None and first.marker.exists()
    # The runtime stopped the first run before it could finish.
    second = hook_journal.HookRun.begin(
        journal, "agent-worktrees", "sessionStart", stale_after_ms=0,
    )
    second.finish(outcome="failure", failure_category="nonzero_exit", exit_code=2)
    events = hook_journal.drain(journal)
    assert [event["outcome"] for event in events] == ["unfinished", "failure"]
    assert events[0]["failure_category"] == "unfinished_prior_run"
    assert events[0]["run_sequence"] == 1
    assert events[1]["run_sequence"] == 2 and events[1]["exit_code"] == 2
    assert set(events[1]) == {
        "kind", "plugin", "entry", "outcome", "failure_category", "exit_code",
        "duration_ms", "run_sequence", "started_at",
    }
    assert hook_journal.drain(journal) == []
    assert not list(journal.glob("*.start"))


def test_hook_journal_does_not_flag_a_concurrent_young_run(tmp_path: Path) -> None:
    journal = tmp_path / "journal"
    first = hook_journal.HookRun.begin(journal, "agent-vault", "sessionStart")
    second = hook_journal.HookRun.begin(journal, "agent-vault", "sessionStart")
    second.finish(outcome="success")
    first.finish(outcome="timeout", failure_category="timeout")
    assert [event["outcome"] for event in hook_journal.drain(journal)] == ["success", "timeout"]


def test_hook_journal_bounds_pending_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook_journal, "PENDING_LIMIT", 400)
    for _ in range(10):
        hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart").finish(outcome="success")
    assert (tmp_path / "pending.jsonl").stat().st_size <= 400
    assert 0 < len(hook_journal.drain(tmp_path)) < 10


def _finish_runs(directory: Path, count: int) -> None:
    for _ in range(count):
        hook_journal.HookRun.begin(directory, "agent-vault", "sessionStart").finish(outcome="success")


def test_limited_drains_keep_the_remainder_pending(tmp_path: Path) -> None:
    _finish_runs(tmp_path, 3)
    assert [event["run_sequence"] for event in hook_journal.drain(tmp_path, limit=2)] == [1, 2]
    assert [event["run_sequence"] for event in hook_journal.drain(tmp_path, limit=2)] == [3]
    assert hook_journal.drain(tmp_path) == []


def test_drain_waits_out_an_in_progress_append_without_losing_it(tmp_path: Path) -> None:
    _finish_runs(tmp_path, 1)
    with hook_journal._journal_lock(tmp_path) as held:
        assert held
        # An appender holds the lock: the drain neither claims nor deletes the journal.
        assert hook_journal.drain(tmp_path) == []
    assert len(hook_journal.drain(tmp_path)) == 1


def test_append_during_a_drain_fails_open_and_keeps_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook_journal, "LOCK_WAIT_S", 0.0)
    with hook_journal._journal_lock(tmp_path) as held:
        assert held
        run = hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart")
        run.finish(outcome="success")
    assert hook_journal.drain(tmp_path) == []


def test_concurrent_writers_never_exceed_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    monkeypatch.setattr(hook_journal, "PENDING_LIMIT", 600)
    monkeypatch.setattr(hook_journal, "LOCK_WAIT_S", 5.0)
    threads = [threading.Thread(target=_finish_runs, args=(tmp_path, 4)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert (tmp_path / "pending.jsonl").stat().st_size <= 600
    assert all(event["outcome"] == "success" for event in hook_journal.drain(tmp_path))


def test_marker_recovery_is_bounded_per_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hook_journal, "RECOVER_LIMIT", 3)
    for _ in range(7):
        hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart")
    hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart", stale_after_ms=0)
    assert len(hook_journal.drain(tmp_path)) == 3
    assert len(list(tmp_path.glob("*.start"))) == 5


def test_full_journal_keeps_stale_markers_for_a_later_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart")
    monkeypatch.setattr(hook_journal, "PENDING_LIMIT", 0)
    hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart", stale_after_ms=0)
    assert len(list(tmp_path.glob("*.start"))) == 2
    monkeypatch.setattr(hook_journal, "PENDING_LIMIT", 65536)
    hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart", stale_after_ms=0)
    assert [event["outcome"] for event in hook_journal.drain(tmp_path)] == ["unfinished"] * 2


def test_marker_storage_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook_journal, "MARKER_LIMIT", 2)
    runs = [hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart") for _ in range(4)]
    assert [run.marker is not None for run in runs] == [True, True, False, False]
    assert len(list(tmp_path.glob("*.start"))) == 2


def test_abandoned_scratch_files_are_reclaimed(tmp_path: Path) -> None:
    hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart")
    old = [tmp_path / f".abandoned{suffix}" for suffix in (".tmp", ".seq", ".pending", ".recovering")]
    young = tmp_path / ".inflight.tmp"
    for path in [*old, young]:
        path.write_bytes(b"x")
    for path in old:
        os.utime(path, (1, 1))
    hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart")
    assert not any(path.exists() for path in old)
    assert young.exists()


def test_a_run_recovered_as_unfinished_is_not_also_reported_complete(tmp_path: Path) -> None:
    slow = hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart")
    hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart", stale_after_ms=0)
    slow.finish(outcome="success")
    assert [event["outcome"] for event in hook_journal.drain(tmp_path)] == ["unfinished"]


def test_interruption_after_completion_append_is_not_reported_unfinished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart")
    real_unlink = Path.unlink

    def interrupted(self: Path, missing_ok: bool = False) -> None:
        if self.suffix == ".recovering":
            raise OSError("interrupted")
        real_unlink(self, missing_ok)

    monkeypatch.setattr(Path, "unlink", interrupted)
    run.finish(outcome="success")
    monkeypatch.setattr(Path, "unlink", real_unlink)
    hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart", stale_after_ms=0)
    assert [event["outcome"] for event in hook_journal.drain(tmp_path)] == ["success"]


def test_hook_duration_ignores_wall_clock_adjustments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wall = iter([1_700_000_000_000, 1_600_000_000_000])
    monotonic = iter([5_000, 5_040])
    monkeypatch.setattr(hook_journal, "_now_ms", lambda: next(wall))
    monkeypatch.setattr(hook_journal, "_monotonic_ms", lambda: next(monotonic))
    hook_journal.HookRun.begin(tmp_path, "agent-vault", "sessionStart").finish(outcome="success")
    (event,) = hook_journal.drain(tmp_path)
    assert event["duration_ms"] == 40
    assert event["started_at"].startswith("2023-11-14T")


def test_absent_policy_is_legacy_and_starts_nothing(tmp_path: Path) -> None:
    assert load_policy(tmp_path) is LEGACY
    owner = FiniteOwner.from_directory(
        tmp_path, source="agent-worktrees", source_schema="worktrees.lifecycle.v1",
    ).start()
    owner.emit({"kind": "pr", "action": "create", "outcome": "success", "reason": "none"})
    owner.close()
    assert owner.owner is None and owner.status()["legacy"] is True


@pytest.mark.parametrize("policy", [
    {"schema_version": 1, "root": "relative"},
    {"schema_version": 2, "root": "/abs"},
    {"schema_version": 1, "root": "/abs", "extra": 1},
])
def test_invalid_policy_refuses_export(tmp_path: Path, policy: dict[str, Any]) -> None:
    (tmp_path / "consumer-telemetry.json").write_text(json.dumps(policy), encoding="utf-8")
    owner = FiniteOwner.from_directory(
        tmp_path, source="agent-codespaces", source_schema="codespaces.lifecycle.v1",
    ).start()
    assert owner.refusal == "invalid_config" and owner.owner is None


def test_finite_owner_delivers_closed_records_to_a_real_receiver(tmp_path: Path) -> None:
    root = _receiver(tmp_path / "harness root")
    install = tmp_path / "install"
    install.mkdir()
    (install / "consumer-telemetry.json").write_text(json.dumps({
        "schema_version": 1, "root": str(root),
    }), encoding="utf-8")
    with FiniteOwner.from_directory(
        install, source="agent-codespaces", source_schema="codespaces.lifecycle.v1",
    ) as owner:
        assert owner.active
        owner.emit({"kind": "stage", "stage": "credential_relay", "outcome": "failure",
                    "reason": "relay_not_listening", "duration_ms": 7, "path": str(root)})
        owner.emit({"kind": "stage", "stage": "ssh", "outcome": "failure",
                    "reason": "made_up"})
    records = _readback(root)
    assert len(records) == 1
    envelope = records[0]["record"]
    assert Path(records[0]["cwd"]) == root
    assert (envelope["source"], envelope["source_schema"]) == (
        "agent-codespaces", "codespaces.lifecycle.v1",
    )
    assert envelope["event"] == {
        "kind": "stage", "stage": "credential_relay", "outcome": "failure",
        "reason": "relay_not_listening", "duration_ms": 7,
    }
    assert str(root) not in json.dumps(envelope)
    stats = owner.status()["counters"]
    assert stats["pipe_writes"] == 1 and stats["dropped_invalid"] == 1


def test_capability_names_only_the_providers_schemas() -> None:
    assert capability(["codespaces.lifecycle.v1"])["source_schemas"] == ["codespaces.lifecycle.v1"]
