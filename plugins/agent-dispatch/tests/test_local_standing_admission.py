"""Ordinary coordinator construction composes real local admission owners."""

from dataclasses import asdict, replace
import json
import subprocess
import time

import pytest

from agent_dispatch import assignment_local_standing as native
from agent_dispatch import assignment_relationship_owner as owner_module
from agent_dispatch import server, worktree_owner_launch
from agent_dispatch.config import Config
from agent_dispatch.overrides import StandingAttachmentSelector, set_standing_attachment_deny
from agent_dispatch.queue import TaskError
from agent_dispatch.registrar_discovery import add_pointer
from tests.test_assignment_relationship_owner import WRITER_IDENTITY
from tests.test_standing_admission import (
    admission as admission,
    materialization as materialization,
)


@pytest.fixture
def local(admission, monkeypatch, tmp_path):
    root = tmp_path / "trusted"
    root.mkdir()
    declared = asdict(admission.declaration)
    materialized = root / declared["materialization_ref"]
    materialized.parent.mkdir()
    materialized.write_bytes(admission.path.read_bytes())
    path = root / "standing.json"
    path.write_text(json.dumps({
        "name": "dispatcher", "kind": "standing-charter", "repos": admission.task.repo,
        "spec": {key: value for key, value in declared.items() if key != "namespace"},
    }), encoding="utf-8")
    base = tmp_path / "registrar-state"
    add_pointer("local-source", root, owner=declared["namespace"], base=base)
    monkeypatch.setenv("AGENT_DISPATCH_REGISTRAR_DIR", str(base))
    monkeypatch.setenv("AGENT_DISPATCH_OVERRIDES", str(admission.config.overrides_path))
    monkeypatch.setattr(native, "resolve_machine", lambda: admission.workspace.machine)
    admission.native_materialization_command = native._materialization_command
    monkeypatch.setattr(native, "_materialization_command", lambda: admission.config.materialization_command)
    monkeypatch.setattr(native, "agent_bridge_launch_prefix", lambda: ("selected-bridge",))
    monkeypatch.setattr(worktree_owner_launch, "retained_selection", lambda: ("selected-worktrees",))
    capture = owner_module.run_background_capture
    admission.bridge_changed = False
    admission.bridge_unavailable = False
    admission.record_head = admission.head["session_id"]
    admission.bridge_status = "running"
    admission.recovery_calls = []
    admission.recovery_receipts = {}
    admission.recovery_reply_lost = False
    admission.automatic_recovery_stopped = False

    def bridge_capture(argv, **kwargs):
        if argv[0] != "selected-bridge":
            if argv[0] == "selected-worktrees" and "embody" in argv:
                admission.calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, json.dumps({
                    "ok": True, "dry_run": True, "native_admission_version": 1,
                    "worktree_id": admission.workspace.worktree, "work_dir": admission.workspace.path,
                    "resume_session": admission.head["session_id"],
                }), "")
            result = capture(argv, **kwargs)
            if argv[0] == "selected-worktrees" and "record" in argv:
                document = json.loads(result.stdout)
                document["worktree"]["last_session_id"] = admission.record_head
                return subprocess.CompletedProcess(argv, 0, json.dumps(document), "")
            return result
        admission.calls.append(argv)
        if admission.bridge_unavailable:
            return subprocess.CompletedProcess(argv, 1, "{}", "original native journal unavailable")
        if "version" in argv:
            result = {"contracts": {
                name: {"version": 1, "capabilities": ["retained-native-recovery"]}
                for name in ("agent-bridge.resume", "agent-bridge.status")
            }}
        elif "resume" in argv:
            document = json.loads(kwargs["input_text"])
            request = document["retained_recovery"]
            admission.recovery_calls.append(document)
            assert request["native_session_id"] == admission.head["session_id"]
            assert request["head_revision"] == admission.head["revision"]
            assert request["journal_key"] == "a" * 64
            result = {"retained_recovery": {
                "version": 1, "operation_id": request["operation_id"], "state": "complete",
                "journal_key": request["journal_key"], "bridge_session_id": "original-bridge",
                "native_session_id": request["native_session_id"],
            }}
            admission.recovery_receipts[request["operation_id"]] = result["retained_recovery"]
            if admission.recovery_reply_lost:
                return subprocess.CompletedProcess(argv, 1, "{}", "reply lost after native acceptance")
        elif "--recovery-operation" in argv:
            result = admission.recovery_receipts[argv[-1]]
        elif argv[-1] == "status":
            result = {
                "service": "agent-bridge", "status": "ok", "ready": True, "draining": False,
                "capabilities": {
                    "worktree_record_delegation": 1, "retained_workspace_creation_writer": 1,
                    "retained_workspace_creation_writer_inspection": 1,
                    "retained_native_session_recovery": 1,
                },
                "slot": {"role": "active", "pid": 42, "active": {"pid": 42, "generation": 1}},
            }
        else:
            assert argv[argv.index("status") + 1] in {
                admission.head["session_id"], "original-bridge",
            }
            result = {
                "session_id": "original-bridge", "acp_session_id": admission.head["session_id"],
                "durable_session_id": admission.head["session_id"],
                "worktree_id": admission.workspace.worktree, "project": admission.workspace.project,
                "target_dir": admission.workspace.path, "status": admission.bridge_status, "read_only": False,
            }
            if "--worktree-writer" in argv:
                result["worktree_writer_observation"] = {
                    "schema": "copilot-extensions.creation-worktree-writer", "version": 1,
                    "key_digest": "a" * 64,
                    "session_id": result["session_id"], "native_session_id": result["acp_session_id"],
                    "worktree_id": result["worktree_id"], "project": result["project"],
                    "worktree_path": result["target_dir"],
                    "worktree_writer": {
                        "version": 1, "identity": WRITER_IDENTITY,
                        "creation_nonce": "other" if admission.bridge_changed else admission.workspace.creation_nonce,
                    },
                    "writer_scope": "executing-process", "writer_contract": admission.support["writer_contract"],
                    "retained_session_writes": 1,
                    "retained_native_resume": 1,
                    "automatic_recovery_stopped": admission.automatic_recovery_stopped,
                    "bridge_instance": {"installation_root": str(tmp_path / "bridge"), "pid": 42, "generation": 1},
                }
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(owner_module, "run_background_capture", bridge_capture)
    monkeypatch.setattr("agent_dispatch.standing_recovery_driver.agent_bridge_launch_prefix",
                        lambda: ("selected-bridge",))
    app = server.build_app(Config(db_path=str(tmp_path / "native.sqlite3")))
    admission.queue = app.state.queue
    admission.owner.queue = admission.queue
    admission.task = admission.queue.reconcile_standing_charter(
        "Dispatcher", admission.declaration, repo=admission.task.repo,
    )
    admission.source = path
    admission.materialized = materialized
    return admission


def reserve(local):
    local.task = local.queue.reserve_standing_charter(
        local.task.id, local.workspace, expected_revision=local.task.standing_revision,
    )
    return local.task


def accept(local):
    return local.queue.accept_standing_charter(
        local.task.id, local.workspace, expected_revision=local.task.standing_revision,
        expected_generation=local.task.generation, owner_session_id=local.head["session_id"],
        expected_head_revision=local.head["revision"],
    )


def test_fresh_default_construction_admits_original_owners_without_factory_or_pool(local):
    assert local.queue._relationship_writer is None
    assert local.queue._relationship_authority is None
    assert isinstance(local.queue._standing_relationship_writer, native.LocalStandingRelationshipWriter)
    assert local.queue.standing_attachment_supported()
    assert local.task.owner is None and local.task.owner_session_id is None
    reserve(local)
    paired = accept(local)
    assert paired.owner_session_id == local.head["session_id"]
    assert local.queue.task_relationship(paired.id)["request"]["authority"] == "local"
    assert local.queue.task_relationship(paired.id)["native_participant"]["journal_key"] == "a" * 64
    finite = local.queue.create("Unchanged finite work", repo=local.task.repo)
    assert local.queue.claim_one("ordinary", task_id=finite.id).id == finite.id
    local.queue.start(finite.id, "ordinary")
    assert local.queue.complete(finite.id, "ordinary").status == "completed"
    assert local.queue.get(paired.id) == paired
    with local.queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM spawn_reservations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_pool_assignments").fetchone()[0] == 0
    assert any("--worktree-writer" in call for call in local.calls)
    assert any("--resident-only" in call for call in local.calls)
    assert any("inspect-materialization" in call for call in local.calls)
    assert not any("create" in call or "resume" in call or "embody" in call for call in local.calls)


def grant_recovery(local):
    from agent_dispatch.queue_standing_recovery import StandingRecoveryGrant

    reserve(local)
    paired = accept(local)
    local.task = local.queue.grant_standing_recovery(
        paired.id, local.workspace,
        StandingRecoveryGrant("original-grant", "operator-approval", time.time() + 600, 2),
        expected_revision=paired.standing_revision, expected_generation=paired.generation,
        owner_session_id=paired.owner_session_id,
    )
    return local.task


def test_default_native_recovery_restores_exact_pairing_without_pool_or_new_workspace(local):
    from agent_dispatch.standing_recovery_driver import recover

    assert recover(local.queue, local.task.id)["state"] == "ungranted"
    paired = grant_recovery(local)
    before_probe = len(local.calls)
    assert recover(local.queue, paired.id)["state"] == "live-or-unconfirmed"
    live_calls = local.calls[before_probe:]
    assert sum(call[0] == "selected-bridge" for call in live_calls) == 1
    assert not any("inspect-materialization" in call for call in live_calls)
    assert not local.recovery_calls
    local.bridge_status = "stopped"
    outcome = recover(local.queue, paired.id, trigger="boot")
    assert outcome["state"] == "complete"
    restored = local.queue.get(paired.id)
    assert (restored.owner, restored.owner_session_id, restored.generation) == (
        paired.owner, paired.owner_session_id, paired.generation,
    )
    assert restored.standing_charter["recovery"]["attempts"] == 1
    assert restored.standing_charter["recovery"]["pending"] is None
    assert len(local.recovery_calls) == 1
    assert not any("create" in call for call in local.calls)
    with local.queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM spawn_reservations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_pool_assignments").fetchone()[0] == 0


def test_lost_native_reply_reconciles_original_operation_after_revocation_without_second_start(local):
    from agent_dispatch.standing_recovery_driver import recover

    paired = grant_recovery(local)
    local.bridge_status, local.recovery_reply_lost = "stopped", True
    with pytest.raises(TaskError, match="reply lost"):
        recover(local.queue, paired.id)
    held = local.queue.get(paired.id)
    pending = held.standing_charter["recovery"]["pending"]
    assert pending["state"] == "unknown"
    local.queue.revoke_standing_recovery(
        paired.id, grant_id="original-grant", expected_revision=held.standing_revision,
    )
    local.automatic_recovery_stopped = True
    local.source.unlink()
    result = recover(local.queue, paired.id)
    assert result["operation_id"] == pending["operation_id"] and result["state"] == "complete"
    assert len(local.recovery_calls) == 1
    final = local.queue.get(paired.id)
    assert final.standing_charter["recovery"]["state"] == "revoked"
    assert final.standing_charter["recovery"]["pending"] is None


def test_native_stop_hold_prevents_even_preparing_a_standing_recovery(local):
    from agent_dispatch.standing_recovery_driver import recover

    paired = grant_recovery(local)
    local.bridge_status, local.automatic_recovery_stopped = "stopped", True
    with pytest.raises(TaskError, match="native automatic recovery is stopped"):
        recover(local.queue, paired.id)
    current = local.queue.get(paired.id)
    assert current.standing_charter["recovery"]["attempts"] == 0
    assert current.standing_charter["recovery"]["pending"] is None
    assert not local.recovery_calls


@pytest.mark.parametrize("withdrawal", ["revoke", "expire"])
def test_restarted_driver_settles_withdrawn_preparation_without_native_call(local, withdrawal):
    from agent_dispatch.queue import TaskQueue
    from agent_dispatch.queue_standing_recovery import StandingRecoveryGrant
    from agent_dispatch.standing_recovery_driver import recover

    paired = grant_recovery(local)
    prepared = local.queue.prepare_standing_recovery(
        paired.id, operation_id="prepared-before-restart", grant_id="original-grant",
        expected_revision=paired.standing_revision, trigger="boot",
    )
    current = local.queue.get(paired.id)
    now = time.time()
    if withdrawal == "revoke":
        local.queue.revoke_standing_recovery(
            paired.id, grant_id="original-grant", expected_revision=current.standing_revision,
        )
    else:
        now = current.standing_charter["recovery"]["request"]["expires_at"] + 1
    restarted = TaskQueue(local.queue.db_path)
    restarted._standing_relationship_writer = native.LocalStandingRelationshipWriter(restarted)
    restarted._now = lambda _now: now
    before = len(local.calls)
    assert recover(restarted, paired.id) == {
        "task_id": paired.id, "state": "not-started", "operation_id": prepared["operation_id"],
    }
    assert len(local.calls) == before
    settled = restarted.get(paired.id)
    assert settled.standing_charter["recovery"]["pending"] is None
    replacement = restarted.grant_standing_recovery(
        paired.id, local.workspace,
        StandingRecoveryGrant("replacement-grant", "new-authority", now + 600, 1),
        expected_revision=settled.standing_revision, expected_generation=settled.generation,
        owner_session_id=settled.owner_session_id,
    )
    assert replacement.standing_charter["recovery"]["request"]["grant_id"] == "replacement-grant"
    assert not local.recovery_calls


@pytest.mark.parametrize("late_native_drift", [False, True])
def test_first_successor_acceptance_refreshes_only_native_observation(local, monkeypatch, late_native_drift):
    reserve(local)
    paired = accept(local)
    before = local.queue.task_relationship(paired.id)
    predecessor = paired.owner_session_id
    local.head.update(
        session_id="native-successor", revision=local.head["revision"] + 1,
        predecessor_session_id=predecessor,
    )
    local.record_head = local.head["session_id"]
    if late_native_drift:
        from contextlib import contextmanager

        writer = local.queue._standing_relationship_writer
        admit = writer.admit_standing

        @contextmanager
        def changed_observation(*args):
            with admit(*args) as receipt:
                retained = local.queue.task_relationship(paired.id)
                local.queue.retain_standing_native_participant(retained["request"], {
                    **retained["native_participant"], "native_session_id": "unadmitted-native",
                })
                yield receipt

        monkeypatch.setattr(writer, "admit_standing", changed_observation)
        with pytest.raises(TaskError, match="relationship changed"):
            local.queue.accept_standing_charter(
                paired.id, local.workspace, expected_revision=paired.standing_revision,
                expected_generation=paired.generation, owner_session_id=local.head["session_id"],
                expected_head_revision=local.head["revision"], expected_owner_session_id=predecessor,
            )
        assert local.queue.get(paired.id) == paired
        return
    successor = local.queue.accept_standing_charter(
        paired.id, local.workspace, expected_revision=paired.standing_revision,
        expected_generation=paired.generation, owner_session_id=local.head["session_id"],
        expected_head_revision=local.head["revision"], expected_owner_session_id=predecessor,
    )
    after = local.queue.task_relationship(paired.id)
    assert successor.owner_session_id == local.head["session_id"]
    assert successor.generation == paired.generation + 1
    assert after["request"] == before["request"] and after["receipt"] == before["receipt"]
    assert after["native_participant"]["native_session_id"] == local.head["session_id"]
    assert after["native_participant"]["journal_key"] == before["native_participant"]["journal_key"]


def test_original_journal_can_observe_its_new_native_without_transferring_assignment(local):
    reserve(local)
    paired = accept(local)
    relationship = local.queue.task_relationship(paired.id)
    participant = relationship["native_participant"]
    successor = {
        **participant, "bridge_session_id": "successor-bridge", "native_session_id": "native-successor",
    }
    assert local.queue.retain_standing_native_participant(relationship["request"], successor) == successor
    assert local.queue.task_relationship(paired.id)["native_participant"] == successor
    assert local.queue.get(paired.id) == paired
    with pytest.raises(TaskError, match="changed its original journal"):
        local.queue.retain_standing_native_participant(
            relationship["request"], {**successor, "journal_key": "b" * 64},
        )


def test_control_authorized_batch_drives_default_consumer_and_preserves_no_grant_tasks(local):
    from tests.test_standing_charter_exposure import CONTROL, DATA, app

    paired = grant_recovery(local)
    local.bridge_status = "stopped"
    with app(local.queue) as api:
        path, body = "/standing/recovery/reconcile", {"cursor": None, "limit": 1}
        assert api.post(path, json=body, headers=DATA).status_code == 403
        assert not local.recovery_calls
        result = api.post(path, json=body, headers=CONTROL)
        assert result.status_code == 200, result.text
        result = result.json()
        assert result["reports"][0]["task_id"] == paired.id
        assert result["reports"][0]["state"] == "complete"
        assert result["next_cursor"] == paired.id
        final = api.post(path, json={**body, "cursor": result["next_cursor"]}, headers=CONTROL)
        assert final.json() == {"version": 1, "reports": [], "next_cursor": None}
    assert len(local.recovery_calls) == 1


def test_missing_materialization_owner_holds_candidate_without_stalling_page(local, monkeypatch):
    from agent_dispatch.registrar import RegistrarError
    from tests.test_standing_charter_exposure import CONTROL, app

    paired = grant_recovery(local)
    local.bridge_status = "stopped"
    monkeypatch.setattr(native, "_materialization_command", local.native_materialization_command)
    monkeypatch.setattr("plugin_activation.resolve_active_plugins", lambda **kwargs: None)

    def missing(*args, **kwargs):
        raise RegistrarError("fixture plugin is disabled")

    monkeypatch.setattr("agent_dispatch.registrar_recipes._resolve_plugin_ref_path", missing)
    with app(local.queue) as api:
        path = "/standing/recovery/reconcile"
        response = api.post(path, json={"cursor": None, "limit": 1}, headers=CONTROL)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["reports"][0]["task_id"] == paired.id
        assert "unavailable" in json.dumps(result)
        assert result["next_cursor"] == paired.id
        final = api.post(path, json={"cursor": paired.id, "limit": 1}, headers=CONTROL)
        assert final.json() == {"version": 1, "reports": [], "next_cursor": None}
    assert not local.recovery_calls


@pytest.mark.parametrize("deadline", [None, 110.0, 90.0])
def test_existing_supervisor_recovers_one_bounded_page_per_cycle_and_retains_cursor(monkeypatch, deadline):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import Mock
    from agent_dispatch.supervisor_sources import SupervisorSources
    monkeypatch.setattr("agent_dispatch.supervisor_sources.time.monotonic", lambda: 100.0)

    recover = Mock(side_effect=[
        {"version": 1, "reports": [], "next_cursor": "next-task"},
        {"version": 1, "reports": [], "next_cursor": None},
        None,
    ])
    owner = SimpleNamespace(
        client=SimpleNamespace(recover_standing_batch=recover),
        _reconcile_call_budget=30,
        _reconcile_deadline=deadline,
        _pool_lock=lambda: SimpleNamespace(guard_current_acquisition=lambda **_: nullcontext()),
    )
    for _ in range(3):
        SupervisorSources._reconcile_standing_recovery(owner)
    if deadline == 90:
        recover.assert_not_called()
        return
    assert [call.kwargs["cursor"] for call in recover.call_args_list] == [None, "next-task", None]
    assert all(call.kwargs["limit"] == 1 for call in recover.call_args_list)
    assert all(call.kwargs["deadline"] == (130 if deadline is None else deadline)
               for call in recover.call_args_list)


def test_recovery_route_rejects_drain_and_counts_an_admitted_operation(local, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from tests.test_standing_charter_exposure import CONTROL, app

    grant_recovery(local)
    entered, release = Event(), Event()

    def recover(_queue, task_id):
        entered.set()
        assert release.wait(5)
        return {"task_id": task_id, "state": "complete"}

    monkeypatch.setattr("agent_dispatch.standing_recovery_driver.recover", recover)
    with app(local.queue) as api, ThreadPoolExecutor(max_workers=1) as pool:
        gate = api.app.state.drain_gate
        path, body = "/standing/recovery/reconcile", {"limit": 1}
        future = pool.submit(api.post, path, json=body, headers=CONTROL)
        try:
            assert entered.wait(5)
            assert gate.claims == 1
            gate.set_draining(True)
            assert gate.wait_for_claims(timeout=0, poll=0.01) is False
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 200
        assert gate.claims == 0
        assert gate.wait_for_claims(timeout=0, poll=0.01) is True
        assert api.post(path, json=body, headers=CONTROL).status_code == 409
        assert gate.claims == 0


def test_legacy_native_recovery_subprocesses_do_not_inherit_coordinator_credentials(local, monkeypatch):
    from agent_dispatch.standing_recovery_driver import recover
    from agent_dispatch import procutil

    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_CONTROL_TOKEN", "fixture-control-authority")
    monkeypatch.setenv("AGENT_DISPATCH_TOKEN", "fixture-data-authority")
    capture = owner_module.run_background_capture
    observed = []

    def inspect_environment(argv, **kwargs):
        if argv[0] in {"selected-bridge", "selected-worktrees"}:
            assert not any(key.upper() in {
                "AGENT_DISPATCH_CONTROL_TOKEN", "AGENT_DISPATCH_TOKEN",
            } for key in kwargs["env"])
            observed.append(argv[0])
        return capture(argv, **kwargs)

    monkeypatch.setattr(owner_module, "run_background_capture", inspect_environment)
    paired = grant_recovery(local)
    local.bridge_status = "stopped"
    assert recover(local.queue, paired.id)["state"] == "complete"
    assert set(observed) == {"selected-bridge", "selected-worktrees"}
    assert procutil.os.environ["AGENT_DISPATCH_CONTROL_TOKEN"] == "fixture-control-authority"


@pytest.mark.parametrize("failure", ["bridge-unavailable", "bridge-incarnation", "foreign-machine", "source"])
def test_default_admission_retains_original_request_on_unconfirmed_owner(local, monkeypatch, failure):
    if failure == "bridge-unavailable":
        local.bridge_unavailable = True
    elif failure == "bridge-incarnation":
        local.bridge_changed = True
    elif failure == "foreign-machine":
        monkeypatch.setattr(native, "resolve_machine", lambda: "different-host")
    else:
        document = json.loads(local.source.read_text(encoding="utf-8"))
        document["spec"]["source_revision"] = "changed"
        local.source.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(TaskError, match="unresolved|host-local owner|enabled trusted declaration"):
        reserve(local)
    retained = local.queue.task_relationship(local.task.id)
    assert retained["state"] == "pending" and retained["receipt"] is None
    assert retained["request"]["workspace"] == asdict(local.workspace)
    assert local.queue.get(local.task.id).owner_session_id is None
    with local.queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM spawn_reservations").fetchone()[0] == 0


def test_local_deny_and_changed_materialization_block_default_acceptance(local):
    reserve(local)
    selector = StandingAttachmentSelector(
        local.declaration.namespace, local.declaration.charter_key, local.declaration.lane_key,
    )
    set_standing_attachment_deny(local.config.overrides_path, selector, reason="operator hold")
    with pytest.raises(TaskError, match="denied|disabled"):
        accept(local)
    assert local.queue.get(local.task.id) == local.task


def test_default_release_uses_original_native_conclusion_without_requiring_enabled_source(local):
    reserve(local)
    paired = accept(local)
    local.source.unlink()
    local.record_head = None
    released = local.queue.release_standing_attachment(
        paired.id, local.workspace, expected_revision=paired.standing_revision,
        expected_generation=paired.generation, owner_session_id=paired.owner_session_id,
        expected_conclusion_revision=local.conclusion["revision"],
    )
    assert released.owner is None and released.owner_session_id is None
    assert local.queue.task_relationship(paired.id)["state"] == "closed"
    assert not any("resume" in call or "embody" in call for call in local.calls)


def test_unaccepted_assignment_can_close_its_original_journal_after_head_conclusion(local):
    reserve(local)
    local.source.unlink()
    local.record_head = None
    cancelled = local.queue.cancel_standing_reservation(
        local.task.id, local.workspace, expected_revision=local.task.standing_revision,
        expected_generation=local.task.generation,
    )
    assert cancelled.owner is None
    assert local.queue.task_relationship(local.task.id)["state"] == "closed"


@pytest.mark.parametrize("reference", ["../outside.json", "/outside.json", "C:\\outside.json", "C:outside.json"])
def test_native_materialization_cannot_escape_source_root(tmp_path, reference):
    with pytest.raises(TaskError, match="source-relative"):
        native._materialization_path(tmp_path, reference)


def test_explicit_alternate_admission_does_not_install_default(monkeypatch, tmp_path):
    selected = object()
    monkeypatch.setattr("agent_dispatch.assignment_admission.configured_writer", lambda *_: selected)
    app = server.build_app(replace(
        Config(db_path=str(tmp_path / "explicit.sqlite3")),
        relationship_admission="configured:factory", relationship_authority="central",
    ))
    assert app.state.queue._relationship_writer is selected
    assert app.state.queue._standing_relationship_writer is None
    assert app.state.queue._relationship_authority == "central"
