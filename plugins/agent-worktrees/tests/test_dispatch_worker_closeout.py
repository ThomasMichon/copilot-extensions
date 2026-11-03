"""Own-worker admission adds preservation gates without releasing Dispatch custody."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from agent_worktrees import dispatch_worker_closeout as closeout
from agent_worktrees import config as cfg, sessions, tracking

NATIVE = "00000000-0000-4000-8000-000000000001"
SOURCE = "native-source-v1:" + "a" * 64


@pytest.fixture
def worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    workspace = tmp_path / "worker"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    record = tracking.WorktreeRecord(
        worktree_id="worker", branch="worktree/worker", worktree_path=str(workspace),
        repo="project", machine="host", platform="linux", started_at="2026-01-01",
        last_resumed_at="2026-01-01", resume_count=0, title=None,
        status="active", completed_at=None, creation_nonce="created-worker",
        sessions=[tracking.SessionEntry(
            session_id=NATIVE, started_at="2026-01-01", activations=[tracking.SessionActivation(
                ordinal=1, started_at="2026-01-01", start_recorded_at="2026-01-01",
                source_identity=SOURCE,
            )],
        )],
        head_session=NATIVE,
        dispatch_attempt=tracking.DispatchAttempt("task", "reservation", 1, "acp", "pool", "host"),
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="host", platform="linux", repo_name="project",
        repos={"project": cfg.RepoConfig(anchor=str(tmp_path / "anchor"), worktree_root=str(tmp_path))},
    )
    binding = closeout.OwnWorker(
        "task", "reservation", NATIVE, "worker", "created-worker", workspace, SOURCE, 0,
        record.dispatch_attempt,
    )
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", NATIVE)
    monkeypatch.setattr(closeout, "_native_source", lambda native: SOURCE if native == NATIVE else None)
    monkeypatch.setattr(closeout, "require_record_mutation", lambda record, config: None)
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda records: sessions.SessionContext())
    return SimpleNamespace(record=record, binding=binding, config=config, root=tmp_path)


def test_own_inbound_allocation_and_session_are_not_an_outbound_closeout_deadlock(worker) -> None:
    record = worker.record
    record.owner_ref = "host/project/creator"
    record.resources.append(tracking.ResourceClaim(
        kind="session", ref=f"host/project/worker#{NATIVE}",
    ))
    worker.binding.require(record, worker.config)


@pytest.mark.parametrize("change", [
    {"task_id": "foreign"}, {"reservation_key": "foreign"}, {"native_id": "foreign"},
    {"worktree_id": "foreign"}, {"creation_nonce": "recreated"}, {"creation_nonce": ""},
    {"source": "foreign"}, {"head_revision": 1},
])
def test_foreign_or_replaced_binding_is_not_own_finalization(worker, change: dict) -> None:
    with pytest.raises(ValueError):
        replace(worker.binding, **change).require(worker.record, worker.config)


@pytest.mark.parametrize("change", [
    {"dispatch_attempt": None}, {"dispatch_attempt_opaque": True},
    {"checkout_managed": False}, {"status": "pushed"}, {"head_session": "foreign"},
    {"follow_up": True}, {"active_effort": "active"},
    {"pair_id": "pair", "pair_role": "harness", "pair_ref": "host/notes/sibling", "pair_kind": "worktree"},
    {"controller_metadata_opaque": True}, {"execution_leg_opaque": True},
    {"session_backend_opaque": True},
])
def test_preservation_states_never_reach_finalization(worker, change: dict) -> None:
    for key, value in change.items():
        setattr(worker.record, key, value)
    with pytest.raises(ValueError):
        worker.binding.require(worker.record, worker.config)


@pytest.mark.parametrize("kind", ["missing", "ambiguous", "ended", "duplicate"])
def test_canonical_active_native_source_is_required(worker, kind: str) -> None:
    entry = worker.record.sessions[0]
    if kind == "missing":
        entry.activations = []
    elif kind == "duplicate":
        entry.activations *= 2
    elif kind == "ambiguous":
        entry.activations[0].source_ambiguous = True
    else:
        entry.activations[0].ended_at = "2026-01-02"
    with pytest.raises(ValueError, match="canonical active"):
        worker.binding.require(worker.record, worker.config)


@pytest.mark.parametrize("kind", ["recorded", "live", "session-claim", "producer", "controller", "handoff"])
def test_foreign_shared_and_unsettled_responsibility_is_retained(
    worker, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    record = worker.record
    if kind == "recorded":
        record.sessions.append(tracking.SessionEntry(session_id="foreign", started_at="2026-01-01"))
    elif kind == "live":
        monkeypatch.setattr(sessions, "scan_sessions_fast", lambda records: sessions.SessionContext(
            active_sessions={sessions._normalize_path(record.worktree_path): [NATIVE, "foreign"]},
        ))
    elif kind in {"session-claim", "producer"}:
        record.resources.append(tracking.ResourceClaim(
            kind="session" if kind == "session-claim" else "container", ref="foreign",
        ))
    elif kind == "controller":
        record.controllers.append(tracking.ControllerRelation(
            kind="session", source="manual", relation_revision=1,
            created_at="2026-01-01", controller_session_id="foreign",
        ))
    else:
        record.handoffs.append(tracking.SessionHandoff(
            ordinal=1, token="pending", predecessor=NATIVE, state="pending", opened_at="2026-01-01",
        ))
    with pytest.raises(ValueError):
        worker.binding.require(record, worker.config)


def test_environment_label_and_neighboring_cwd_are_not_native_ownership(worker, monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "foreign")
    with pytest.raises(ValueError):
        worker.binding.require(worker.record, worker.config)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", NATIVE)
    monkeypatch.chdir(worker.root)
    with pytest.raises(ValueError):
        worker.binding.require(worker.record, worker.config)


@pytest.mark.parametrize("change", [{"attempt": 2}, {"driver": "cli"}, {"supervisor": "other"}])
def test_same_task_and_key_cannot_replace_the_original_allocation(worker, change: dict) -> None:
    worker.record.dispatch_attempt = replace(worker.record.dispatch_attempt, **change)
    with pytest.raises(ValueError):
        worker.binding.require(worker.record, worker.config)


def _wire(worker, monkeypatch) -> list:
    monkeypatch.setattr(cfg, "load_config", lambda: worker.config)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: worker.root)
    monkeypatch.setattr(closeout, "read_workspace_record", lambda cwd: {"worktree": {
        "id": worker.record.worktree_id, "path": worker.record.worktree_path,
    }})
    monkeypatch.setattr(tracking, "load_record", lambda path: worker.record)
    monkeypatch.setattr(
        closeout.finalized_checkout, "matches",
        lambda record: record.status == "finalized" and record.finalized_checkout is not None,
    )
    calls = []

    def finalize(worktree_id, config, **kwargs):
        calls.append((worktree_id, kwargs))
        kwargs["record_guard"](worker.record)
        worker.record.status = "finalized"
        worker.record.finalized_checkout = {"head": "a" * 40, "branch": worker.record.branch}
        return True

    monkeypatch.setattr(closeout.finalize, "validate_and_finalize", finalize)
    return calls


def test_finalizer_is_additive_and_never_asks_to_abandon_or_release(worker, monkeypatch) -> None:
    calls = _wire(worker, monkeypatch)
    result = closeout.finalize_own("task", "reservation", NATIVE)
    assert len(calls) == 1 and calls[0][0] == "worker"
    assert set(calls[0][1]) == {"record_guard"}
    assert result["status"] == "finalized" and result["checkout_retained"] is True
    assert result["task_release"] is False and result["process_retirement"] is False
    assert result["task_id"] == "task" and result["reservation_key"] == "reservation"
    assert result["creation_nonce"] == "created-worker" and result["native_id"] == NATIVE


@pytest.mark.parametrize("fault", ["normal-gate", "missing-stamp", "unresolved", "foreign", "no-worktree"])
def test_failed_or_unconfirmed_closeout_does_not_emit_success(worker, monkeypatch, fault) -> None:
    calls = _wire(worker, monkeypatch)
    if fault == "normal-gate":
        monkeypatch.setattr(closeout.finalize, "validate_and_finalize", lambda *a, **k: False)
    elif fault == "missing-stamp":
        def no_stamp(*args, **kwargs):
            worker.record.status = "finalized"
            return True
        monkeypatch.setattr(closeout.finalize, "validate_and_finalize", no_stamp)
    elif fault == "unresolved":
        worker.record.status = "finalizing"
    elif fault == "foreign":
        worker.record.dispatch_attempt = replace(worker.record.dispatch_attempt, task_id="other")
    else:
        monkeypatch.setattr(closeout, "read_workspace_record", lambda cwd: {"worktree": None})
    with pytest.raises(ValueError):
        closeout.finalize_own("task", "reservation", NATIVE)
    assert calls == []


def test_selected_provider_discovery_is_read_only_and_parser_is_strict(monkeypatch, capfd) -> None:
    monkeypatch.setattr(closeout, "finalize_own", lambda *a: pytest.fail("Discovery finalized a workspace"))
    assert closeout.main(["--describe-contract"]) == 0
    descriptor = json.loads(capfd.readouterr().out)
    assert descriptor["contract"] == closeout.CONTRACT and descriptor["version"] == 1
    for argv in (
        ["--describe-contract", "--task-id", "task"], ["--typo"],
        ["--task-id", "task", "--reservation-key", "reservation"],
    ):
        with pytest.raises(SystemExit) as exc:
            closeout.main(argv)
        assert exc.value.code == 2


def test_native_process_proof_never_trusts_the_environment_label(monkeypatch) -> None:
    witness = {"qualified": False}
    seen = []
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", NATIVE)
    monkeypatch.setattr(closeout.native_source_context, "capture", lambda: witness)
    monkeypatch.setattr(
        closeout.native_lifecycle_source, "start_identity",
        lambda native, source: seen.append((native, source)),
    )
    with pytest.raises(ValueError, match="actual native caller"):
        closeout._native_source(NATIVE)
    assert seen == [(NATIVE, witness)]


def test_failed_cli_closeout_is_structured_without_claiming_release(monkeypatch, capfd) -> None:
    def blocked(*args):
        print("Source validation remains blocked.")
        raise ValueError("Unmerged source remains")

    monkeypatch.setattr(closeout, "finalize_own", blocked)
    assert closeout.main([
        "--task-id", "task", "--reservation-key", "reservation", "--native-id", NATIVE,
    ]) == 1
    captured = capfd.readouterr()
    result = json.loads(captured.out)
    assert result["ok"] is False and result["error"] == "Unmerged source remains"
    assert result["task_release"] is False and result["process_retirement"] is False
    assert "Source validation remains blocked." in captured.err


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Native holds require Linux procfs")
@pytest.mark.parametrize("state", ["concluded", "handed-off"])
def test_actual_foreign_hold_blocks_even_without_legacy_lock_or_active_claim(
    worker, monkeypatch, state,
) -> None:
    other = "00000000-0000-4000-8000-000000000002"
    worker.record.sessions.append(tracking.SessionEntry(
        session_id=other, started_at="2026-01-01", state=state,
    ))
    worker.record.resources.append(tracking.ResourceClaim(
        kind="session", ref=f"host/project/worker#{other}", state="at-rest",
    ))
    root = worker.root / "sessions"
    directory = root / other
    directory.mkdir(parents=True)
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: root)
    with (directory / f"inuse.{os.getpid()}.hold").open("w"):
        assert not list(directory.glob("*.lock"))
        with pytest.raises(ValueError, match="foreign native"):
            worker.binding.require(worker.record, worker.config)
    worker.binding.require(worker.record, worker.config)


@pytest.mark.parametrize("fault", ["unknown", "session-budget", "file-budget", "hold-budget", "unreadable"])
def test_foreign_hold_inspection_never_treats_unknown_or_over_budget_as_absence(
    worker, monkeypatch, fault,
) -> None:
    from agent_worktrees import dispatch_worker_sessions as inspection

    other = "00000000-0000-4000-8000-000000000002"
    worker.record.sessions.append(tracking.SessionEntry(
        session_id=other, started_at="2026-01-01", state="concluded",
    ))
    root = worker.root / "sessions"
    directory = root / other
    directory.mkdir(parents=True)
    (directory / "inuse.123.hold").touch()
    monkeypatch.setattr(sessions, "_session_state_dir", lambda: root)
    monkeypatch.setattr(inspection.native_source_context, "process", lambda pid: {"start_time": "1"})
    monkeypatch.setattr(inspection.session_liveness, "session_process_hold_state", lambda *a: "unknown")
    if fault.endswith("-budget"):
        monkeypatch.setattr(inspection, {
            "session-budget": "MAX_SESSIONS", "file-budget": "MAX_FILES", "hold-budget": "MAX_HOLDS",
        }[fault], 0)
    elif fault == "unreadable":
        def denied(path):
            raise PermissionError("Unreadable native session directory")
        monkeypatch.setattr(inspection.os, "scandir", denied)
    with pytest.raises(ValueError):
        worker.binding.require(worker.record, worker.config)
