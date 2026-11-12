"""Standing admission consumes the actual materialization CLI and native owner contract."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from agent_dispatch import assignment_relationship_owner as module
from agent_dispatch.assignment_admission import StandingAdmission
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.overrides import StandingAttachmentSelector, set_standing_attachment_deny
from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.queue_standing_charters import StandingCharterDeclaration
from tests.test_assignment_relationship_owner import WRITER_IDENTITY, support
from tests.test_assignment_relationships import GroundOwner


@pytest.fixture(scope="module")
def materialization(tmp_path_factory):
    skill = Path(__file__).resolve().parents[2] / "agent-roles" / "skills" / "managing-agent-topology"
    topology = json.loads((skill / "assets" / "topology.example.json").read_text(encoding="utf-8"))
    source = tmp_path_factory.mktemp("standing-source") / "topology.json"
    source.write_text(json.dumps(topology), encoding="utf-8")
    command = (sys.executable, str(skill / "scripts" / "topology.py"))
    result = module.run_background_capture(
        [*command, "materialize", str(source), "--request", topology["assignments"][0]["requestId"]],
        input_text="", timeout=15,
    )
    assert result is not None and result.returncode == 0, result
    return command, json.loads(result.stdout)


@pytest.fixture
def admission(monkeypatch, tmp_path, materialization):
    command, document = materialization
    path = tmp_path / "materialization.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    declaration = StandingCharterDeclaration(
        namespace="operator", charter_key="dispatcher", lane_key="primary",
        effort_ref="efforts/dispatcher", materialization_ref="roles/declared-dispatcher",
        source_revision=document["sourceRevision"],
        expected_materialization_revision=document["materializationRevision"],
    )
    owner = GroundOwner()
    queue = TaskQueue(
        tmp_path / "queue.sqlite3", relationship_writer=owner, relationship_authority="local",
    )
    owner.queue = queue
    workspace = AssignmentWorkspace(
        document["source"]["machine"], "example", "workspace", str(tmp_path / "checkout"), "nonce",
    )
    task = queue.reconcile_standing_charter("Dispatcher", declaration, repo="owner/repo")
    task = queue.reserve_standing_charter(task.id, workspace, expected_revision=task.standing_revision)
    original = queue.task_relationship(task.id)["request"]
    config = StandingAdmission(
        workspace.machine, tmp_path / "overrides.json", command,
        lambda reference: path if reference == declaration.materialization_ref else None,
    )
    state = SimpleNamespace(
        queue=queue, owner=owner, workspace=workspace, task=task, request=original,
        config=config, path=path, declaration=declaration, calls=[], mutate=None,
        support={
            **support(), "head_inspect": 1, "resident_inspect": 1, "succession_inspect": 1,
            "conclusion_inspect": 1,
        },
        head={"session_id": "native-session", "revision": 1}, refuse_request=False,
        conclusion={"session_id": "native-session", "revision": 2},
    )
    actual_capture = module.run_background_capture

    def capture(argv, **kwargs):
        state.calls.append(argv)
        if argv[0] != "selected-worktrees":
            return actual_capture(argv, **kwargs)
        if "capabilities" in argv:
            result = state.support
        elif "record" in argv:
            result = {"ok": True, "worktree": {
                "id": workspace.worktree, "repo": workspace.project,
                "path": workspace.path, "creation_nonce": workspace.creation_nonce,
            }}
        else:
            operation = argv[argv.index("resource-relationship") + 1]
            payload = json.loads(kwargs["input_text"])
            result = owner(workspace, operation, payload)
            result["writer_identity"] = dict(WRITER_IDENTITY)
            if "expected_head" in payload:
                assert "allow_missing" not in payload
                result["head"] = copy.deepcopy(state.head)
            if "expected_conclusion" in payload:
                assert "allow_missing" not in payload and "expected_head" not in payload
                result["conclusion"] = copy.deepcopy(state.conclusion)
            if "--resident-only" in argv:
                result["resident"] = {"generation": "resident", "runtime_generation": "runtime"}
                result["writer_identity"]["runtime_generation"] = "2" * 32
            if state.mutate:
                state.mutate(argv, result)
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(module, "run_background_capture", capture)

    def admit_request(observed, identity, request):
        assert observed == workspace and identity == WRITER_IDENTITY and request == original
        if state.refuse_request:
            raise TaskError("independent owner refused the request")
        request["task_id"] = "callback-mutation"

    state.writer = module.WorktreeRelationshipWriter(
        ["selected-worktrees"], admit_workspace=lambda observed, identity: (
            None if observed == workspace and identity == WRITER_IDENTITY
            else pytest.fail("workspace identity drifted")
        ),
        admit_request=admit_request, standing=config, require_resident=True,
    )
    state.queue = TaskQueue(
        queue.db_path, relationship_writer=state.writer, relationship_authority="local",
    )
    owner.queue = state.queue
    return state


def accept(admission, **changes):
    arguments = {
        "workspace": admission.workspace,
        "expected_revision": admission.task.standing_revision,
        "expected_generation": admission.task.generation,
        "owner_session_id": admission.head["session_id"],
        "expected_head_revision": admission.head["revision"],
        **changes,
    }
    return admission.queue.accept_standing_charter(admission.task.id, **arguments)


def prepare_successor(admission):
    admission.task = accept(admission)
    previous = admission.task.owner_session_id
    admission.head = {
        "session_id": "native-successor", "revision": 2, "predecessor_session_id": previous,
    }
    return {"expected_owner_session_id": previous}


def release(admission, **changes):
    return admission.queue.release_standing_attachment(admission.task.id, **{
        "workspace": admission.workspace,
        "expected_revision": admission.task.standing_revision,
        "expected_generation": admission.task.generation,
        "owner_session_id": admission.task.owner_session_id,
        "expected_conclusion_revision": admission.conclusion["revision"],
        **changes,
    })


def cancel(admission, **changes):
    return admission.queue.cancel_standing_reservation(admission.task.id, **{
        "workspace": admission.workspace,
        "expected_revision": admission.task.standing_revision,
        "expected_generation": admission.task.generation,
        **changes,
    })


def test_actual_materialization_and_exact_native_head_produce_admission_only(admission):
    before = admission.queue.get(admission.task.id)
    original = copy.deepcopy(admission.request)
    with admission.writer.admit_standing(
        admission.request, asdict(admission.declaration), admission.head,
    ) as accepted:
        assert accepted["head"] == admission.head
        assert accepted["request_id"] == original["request_id"]
        assert accepted["assignment_event_id"] == original["event_id"]
        assert accepted["generation"] == original["generation"]
        assert accepted["materialization_revision"] == admission.declaration.expected_materialization_revision
        assert accepted["local_policy"]["selector"] == {
            "namespace": "operator", "charter_key": "dispatcher", "lane_key": "primary",
        }
    assert admission.request == original
    assert admission.queue.get(admission.task.id) == before
    assert any("inspect-materialization" in call for call in admission.calls)
    assert sum("--resident-only" in call for call in admission.calls) == 1


def test_finite_liveness_recovery_preserves_accepted_standing_owner(admission):
    accepted = accept(admission)
    finite_queue = TaskQueue(
        admission.queue.db_path, relationship_writer=admission.owner, relationship_authority="local",
    )
    finite = finite_queue.create("Finite work", repo="owner/repo")
    finite_queue.claim_one(
        "m/wt", task_id=finite.id, assignment_workspace=AssignmentWorkspace(
            "m", "example", "wt", admission.workspace.path, "finite",
        ),
    )
    finite_queue.start(finite.id, "m/wt", owner_session_id="finite-session")
    probed = []

    counts = admission.queue.reconcile_liveness(
        lambda worktree, machine, session: probed.append(session) or "gone",
        headless_local_verdict=lambda _session: "gone",
        headless_fleet_verdict=lambda _handle, _session: "gone",
    )

    assert probed == ["finite-session"]
    assert counts["checked"] == 1 and counts["suspended"] == 1
    assert admission.queue.get(finite.id).status == "suspended"
    assert admission.queue.get(accepted.id) == accepted


@pytest.mark.parametrize("boundary", ["resident", "direct"])
@pytest.mark.parametrize("change", ["missing", "session", "revision", "boolean"])
def test_checked_head_refuses_missing_stale_and_boolean_revisions(admission, boundary, change):
    def mutate(argv, result):
        if ("--resident-only" in argv) != (boundary == "resident"):
            return
        if change == "missing":
            result.pop("head")
        elif change == "session":
            result["head"]["session_id"] = "successor"
        elif change == "revision":
            result["head"]["revision"] += 1
        else:
            result["head"]["revision"] = True

    admission.mutate = mutate
    with pytest.raises(TaskError, match="unconfirmed|did not confirm"):
        admission.writer.inspect_head(admission.request, admission.head)
    if boundary == "resident":
        assert not any("record" in call for call in admission.calls)


def test_checked_head_retains_full_original_relationship_and_independent_admission(admission):
    def mutate(argv, result):
        if "--resident-only" not in argv:
            result["producer"]["identity"]["assignment_event_id"] += 1

    admission.mutate = mutate
    with pytest.raises(TaskError, match="original admitted assignment"):
        admission.writer.inspect_head(admission.request, admission.head)
    admission.mutate = None
    admission.refuse_request = True
    calls = len(admission.calls)
    with pytest.raises(TaskError, match="independent owner refused"):
        admission.writer.inspect_head(admission.request, admission.head)
    assert len(admission.calls) == calls


def test_checked_conclusion_is_read_only_and_preserves_the_original_request(admission):
    task = admission.queue.get(admission.task.id)
    events = admission.queue.events(task.id)
    relationship = admission.queue.task_relationship(task.id)
    original = copy.deepcopy(admission.request)
    conclusion = copy.deepcopy(admission.conclusion)
    calls = len(admission.owner.calls)
    receipt = admission.writer.inspect_conclusion(admission.request, admission.conclusion)
    assert receipt["conclusion"] == conclusion
    assert receipt["ref"] == relationship["receipt"]["ref"]
    assert admission.request == original and admission.conclusion == conclusion
    assert admission.queue.get(task.id) == task
    assert admission.queue.events(task.id) == events
    assert admission.queue.task_relationship(task.id) == relationship
    assert admission.queue.attachment_history(task.id) == []
    assert {operation for operation, _ in admission.owner.calls[calls:]} == {"inspect"}
    assert sum("--resident-only" in call for call in admission.calls) == 1


@pytest.mark.parametrize("boundary", ["resident", "direct"])
@pytest.mark.parametrize("change", ["missing", "session", "revision", "boolean"])
def test_checked_conclusion_requires_exact_native_settlement(admission, boundary, change):
    def mutate(argv, result):
        if ("--resident-only" in argv) != (boundary == "resident"):
            return
        if change == "missing":
            result.pop("conclusion")
        elif change == "session":
            result["conclusion"]["session_id"] = "successor"
        elif change == "revision":
            result["conclusion"]["revision"] += 1
        else:
            result["conclusion"]["revision"] = True

    admission.mutate = mutate
    with pytest.raises(TaskError, match="unconfirmed|did not confirm"):
        admission.writer.inspect_conclusion(admission.request, admission.conclusion)
    if boundary == "resident":
        assert not any("record" in call for call in admission.calls)


@pytest.mark.parametrize("conclusion", [
    None, {}, {"session_id": "native-session"}, {"session_id": "", "revision": 2},
    {"session_id": " native-session", "revision": 2}, {"session_id": True, "revision": 2},
    {"session_id": "native-session", "revision": True},
    {"session_id": "native-session", "revision": 0},
    {"session_id": "native-session", "revision": 2, "predecessor_session_id": "predecessor"},
])
def test_invalid_conclusion_guards_are_rejected_before_native_calls(admission, conclusion):
    calls = len(admission.calls)
    with pytest.raises(TaskError, match="exact native conclusion"):
        admission.writer.inspect_conclusion(admission.request, conclusion)
    assert len(admission.calls) == calls


@pytest.mark.parametrize("capability", [None, True, 0, 2])
def test_old_writer_refuses_conclusion_without_losing_finite_inspection(admission, capability):
    admission.support["conclusion_inspect"] = capability
    writer = module.WorktreeRelationshipWriter(
        ["selected-worktrees"], admit_workspace=lambda *_: None,
        admit_request=lambda *_: None, require_resident=True,
    )
    assert writer.bind_request(admission.request)(
        admission.workspace, "inspect",
        {"request_id": admission.request["request_id"], "allow_missing": True},
    )["phase"] == "bound"
    calls = len(admission.calls)
    with pytest.raises(TaskError, match="exact native conclusion"):
        writer.inspect_conclusion(admission.request, admission.conclusion)
    assert len(admission.calls) == calls


def test_checked_conclusion_keeps_original_relationship_and_independent_admission(admission):
    def mutate(argv, result):
        if "--resident-only" not in argv:
            result["producer"]["identity"]["assignment_event_id"] += 1

    admission.mutate = mutate
    with pytest.raises(TaskError, match="original admitted assignment"):
        admission.writer.inspect_conclusion(admission.request, admission.conclusion)
    admission.mutate = None
    admission.refuse_request = True
    calls = len(admission.calls)
    with pytest.raises(TaskError, match="independent owner refused"):
        admission.writer.inspect_conclusion(admission.request, admission.conclusion)
    assert len(admission.calls) == calls


@pytest.mark.parametrize("retiring", [False, True])
@pytest.mark.parametrize("succeeding", [False, True])
def test_release_settles_original_relationship_before_reuse_or_retirement(
    admission, retiring, succeeding,
):
    arguments = prepare_successor(admission) if succeeding else {}
    admission.task = accept(admission, **arguments)
    admission.conclusion = {
        "session_id": admission.task.owner_session_id, "revision": admission.head["revision"] + 1,
    }
    if retiring:
        admission.task = admission.queue.retire_standing_charter(
            admission.task.id, expected_revision=admission.task.standing_revision,
        )
    before_events = admission.queue.events(admission.task.id)
    task = release(admission)
    assert task.status == "queued" and task.owner is None and task.owner_session_id is None
    assert task.generation == admission.task.generation + 1
    assert task.standing_revision == admission.task.standing_revision + 1
    assert task.claimed_at is None and task.started_at is None and task.last_seen_at is None
    assert task.standing_charter["accepted_materialization"] is None
    assert "reservation" not in task.standing_charter
    assert len(admission.queue.events(task.id)) == len(before_events) + 1
    with admission.queue._connect() as conn:
        event = conn.execute(
            "SELECT * FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT 1", (task.id,),
        ).fetchone()
    assert event["unassigned_worker"] == admission.workspace.worker_id
    assert json.loads(event["standing_change"])["action"] == "release"
    relationship = admission.queue.task_relationship(task.id)
    assert relationship["state"] == "closed"
    assert relationship["request"] == admission.request
    assert relationship["receipt"]["producer"]["closeout"]["relationship_verification"]["retired"]
    attachments = admission.queue.attachment_history(task.id)
    assert all(item.detached_at is not None for item in attachments)
    assert attachments[0].detach_reason == "standing release"
    if retiring:
        retired = admission.queue.settle_standing_retirement(
            task.id, expected_revision=task.standing_revision,
        )
        assert retired.standing_charter["lifecycle"] == "retired"
    else:
        queue = TaskQueue(
            admission.queue.db_path, relationship_writer=admission.owner, relationship_authority="local",
        )
        reserved = queue.reserve_standing_charter(
            task.id, admission.workspace, expected_revision=task.standing_revision,
        )
        assert reserved.generation == task.generation + 1
        assert admission.queue.task_relationship(task.id)["request"] != admission.request
        with pytest.raises(TaskError):
            release(admission)
        assert admission.queue.get(task.id) == reserved


@pytest.mark.parametrize("changes", [
    {"expected_revision": True}, {"expected_generation": True}, {"expected_conclusion_revision": True},
    {"expected_revision": 999}, {"expected_generation": 999}, {"expected_conclusion_revision": 999},
    {"expected_conclusion_revision": 1}, {"owner_session_id": "unconfirmed-owner"},
    {"owner_session_id": ""}, {"owner_session_id": " native-session"},
])
def test_release_refuses_stale_or_ambiguous_fences_without_detachment(admission, changes):
    admission.task = accept(admission)
    events = admission.queue.events(admission.task.id)
    attachments = admission.queue.attachment_history(admission.task.id)
    relationship = admission.queue.task_relationship(admission.task.id)
    with pytest.raises(TaskError):
        release(admission, **changes)
    assert admission.queue.get(admission.task.id) == admission.task
    assert admission.queue.events(admission.task.id) == events
    assert admission.queue.attachment_history(admission.task.id) == attachments
    assert admission.queue.task_relationship(admission.task.id) == relationship


@pytest.mark.parametrize("boundary", ["close", "notify"])
def test_release_replays_only_original_closure_after_reply_loss_and_restart(
    admission, monkeypatch, boundary,
):
    admission.task = accept(admission)
    if boundary == "close":
        admission.owner.lose_reply = "close"
    else:
        def lose_reply():
            raise TimeoutError("release committed; response lost")

        monkeypatch.setattr(admission.queue, "_notify_owned_transition", lose_reply)
    with pytest.raises((TaskError, TimeoutError), match="unresolved|response lost"):
        release(admission)
    released = admission.queue.get(admission.task.id)
    events = admission.queue.events(admission.task.id)
    attachments = admission.queue.attachment_history(admission.task.id)
    relationship = admission.queue.task_relationship(admission.task.id)
    assert relationship["state"] == ("bound" if boundary == "close" else "closed")
    assert admission.owner.claims[admission.request["request_id"]]["phase"] == "closed"
    if boundary == "close":
        with pytest.raises(TaskError, match="relationship|obligation"):
            admission.queue.reserve_standing_charter(
                released.id, admission.workspace, expected_revision=released.standing_revision,
            )
    admission.queue = TaskQueue(
        admission.queue.db_path, relationship_writer=admission.writer, relationship_authority="local",
    )
    admission.owner.queue = admission.queue

    def forbid_observation(*_):
        pytest.fail("release replay must not re-observe an already-closed native relationship")

    monkeypatch.setattr(admission.writer, "inspect_conclusion", forbid_observation)
    with pytest.raises(TaskError):
        release(admission, expected_conclusion_revision=admission.conclusion["revision"] + 1)
    assert release(admission) == released
    assert admission.queue.task_relationship(released.id)["state"] == "closed"
    assert admission.queue.events(released.id) == events
    assert admission.queue.attachment_history(released.id) == attachments


def test_release_preserves_hold_and_retries_closure_without_admitting_new_work(admission, monkeypatch):
    admission.task = accept(admission)
    set_standing_attachment_deny(
        admission.config.overrides_path,
        StandingAttachmentSelector("operator", "dispatcher", "primary"), reason="no new attachments",
    )

    def refuse_admission():
        raise TaskError("new work is disabled")

    monkeypatch.setattr(admission.queue, "require_worker_admission", refuse_admission)
    admission.queue.set_hold(admission.task.id, reason="operator hold", actor="operator")
    with pytest.raises(TaskError, match="released; original relationship close unresolved"):
        release(admission)
    released = admission.queue.get(admission.task.id)
    assert released.hold_reason == "operator hold" and released.owner_session_id is None
    assert admission.queue.task_relationship(released.id)["state"] == "bound"
    assert admission.owner.claims[admission.request["request_id"]]["phase"] == "bound"
    with pytest.raises(TaskError, match="released; original relationship close unresolved"):
        release(admission)
    admission.queue.clear_hold(released.id, actor="operator")
    events = admission.queue.events(released.id)
    attachments = admission.queue.attachment_history(released.id)

    def forbid_observation(*_):
        pytest.fail("settlement replay must reuse its original conclusion")

    monkeypatch.setattr(admission.writer, "inspect_conclusion", forbid_observation)
    assert release(admission).hold_reason is None
    assert admission.queue.task_relationship(released.id)["state"] == "closed"
    assert admission.queue.events(released.id) == events
    assert admission.queue.attachment_history(released.id) == attachments


@pytest.mark.parametrize("change", ["declaration", "retirement", "relationship"])
def test_release_rechecks_custody_after_native_observation(admission, change):
    admission.task = accept(admission)
    attachments = admission.queue.attachment_history(admission.task.id)

    def mutate(argv, result):
        if "--resident-only" in argv:
            return
        admission.mutate = None
        if change == "declaration":
            admission.queue.reconcile_standing_charter(
                "Dispatcher", replace(admission.declaration, source_revision="new-source"),
                repo=admission.task.repo, expected_revision=admission.task.standing_revision,
            )
        elif change == "retirement":
            admission.queue.retire_standing_charter(
                admission.task.id, expected_revision=admission.task.standing_revision,
            )
        else:
            with admission.queue._connect() as conn:
                conn.execute(
                    "UPDATE task_events SET task_relationship=json_set(task_relationship, '$.state', 'pending')"
                    " WHERE id=?", (admission.request["event_id"],),
                )

    admission.mutate = mutate
    with pytest.raises(TaskError, match="revision changed|original relationship"):
        release(admission)
    current = admission.queue.get(admission.task.id)
    assert current.owner_session_id == admission.task.owner_session_id
    assert current.generation == admission.task.generation and current.status == "started"
    assert admission.queue.attachment_history(current.id) == attachments


def test_concurrent_same_release_records_one_detachment(admission, monkeypatch):
    admission.task = accept(admission)
    barrier = Barrier(2)
    observe = admission.writer.inspect_conclusion

    def raced(*args):
        receipt = observe(*args)
        barrier.wait(timeout=5)
        return receipt

    monkeypatch.setattr(admission.writer, "inspect_conclusion", raced)
    events = admission.queue.events(admission.task.id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: release(admission), range(2)))
    assert results[0] == results[1]
    assert len(admission.queue.events(admission.task.id)) == len(events) + 1
    assert len(admission.queue.attachment_history(admission.task.id)) == 1
    assert admission.queue.task_relationship(admission.task.id)["state"] == "closed"


def test_release_rollback_preserves_owner_relationship_and_attachment(admission, monkeypatch):
    admission.task = accept(admission)
    record = admission.queue._record_attachment
    events = admission.queue.events(admission.task.id)
    relationship = admission.queue.task_relationship(admission.task.id)
    attachments = admission.queue.attachment_history(admission.task.id)

    def fail_after_record(*args, **kwargs):
        record(*args, **kwargs)
        raise OSError("simulated storage error")

    monkeypatch.setattr(admission.queue, "_record_attachment", fail_after_record)
    with pytest.raises(OSError, match="simulated storage error"):
        release(admission)
    assert admission.queue.get(admission.task.id) == admission.task
    assert admission.queue.events(admission.task.id) == events
    assert admission.queue.task_relationship(admission.task.id) == relationship
    assert admission.queue.attachment_history(admission.task.id) == attachments
    assert admission.owner.claims[admission.request["request_id"]]["phase"] == "bound"
    monkeypatch.setattr(admission.queue, "_record_attachment", record)
    assert release(admission).owner_session_id is None


@pytest.mark.parametrize("retirement", [None, "retire", "disable", "remove"])
def test_cancel_settles_unaccepted_reservation_before_retirement_or_reuse(admission, retirement):
    entry = {"title": "Dispatcher", "declaration": asdict(admission.declaration)}

    def source(entries, revision, expected):
        return admission.queue.reconcile_standing_source_set(
            "owner/repo", admission.declaration.namespace, entries,
            revision=revision, expected_observation=expected,
        )

    if retirement == "retire":
        admission.queue.retire_standing_charter(
            admission.task.id, expected_revision=admission.task.standing_revision,
        )
    elif retirement is not None:
        source([entry], "commit-1", None)
        entries = [{**entry, "enabled": False}] if retirement == "disable" else []
        assert source(entries, "commit-2", "commit-1")["retired"] == [
            {"id": admission.task.id, "reason": f"{retirement}d"},
        ]
    admission.task = admission.queue.get(admission.task.id)
    if retirement is not None:
        with pytest.raises(TaskError, match="not open"):
            accept(admission)
        with pytest.raises(TaskError, match="native owner"):
            admission.queue.settle_standing_retirement(
                admission.task.id, expected_revision=admission.task.standing_revision,
            )
    events = admission.queue.events(admission.task.id)
    calls, owner_calls = len(admission.calls), len(admission.owner.calls)
    task = cancel(admission)
    assert task.status == "queued" and task.owner is None and task.owner_session_id is None
    assert task.generation == admission.task.generation + 1
    assert task.standing_revision == admission.task.standing_revision + 1
    assert task.claimed_at is None and "reservation" not in task.standing_charter
    assert task.standing_charter["lifecycle"] == admission.task.standing_charter["lifecycle"]
    assert len(admission.queue.events(task.id)) == len(events) + 1
    with admission.queue._connect() as conn:
        event = conn.execute(
            "SELECT * FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT 1", (task.id,),
        ).fetchone()
    assert event["unassigned_worker"] == admission.workspace.worker_id
    assert json.loads(event["standing_change"])["action"] == "cancel"
    relationship = admission.queue.task_relationship(task.id)
    assert relationship["state"] == "closed" and relationship["request"] == admission.request
    assert admission.owner.claims[admission.request["request_id"]]["phase"] == "closed"
    assert admission.queue.attachment_history(task.id) == []
    assert not any("inspect-materialization" in call for call in admission.calls[calls:])
    native = admission.owner.calls[owner_calls:]
    assert {operation for operation, _ in native} <= {"inspect", "close"}
    assert not any({"expected_head", "expected_conclusion"} & payload.keys() for _, payload in native)
    assert cancel(admission) == task
    assert len(admission.queue.events(task.id)) == len(events) + 1
    if retirement is None:
        queue = TaskQueue(
            admission.queue.db_path, relationship_writer=admission.owner, relationship_authority="local",
        )
        reserved = queue.reserve_standing_charter(
            task.id, admission.workspace, expected_revision=task.standing_revision,
        )
        assert reserved.generation == task.generation + 1
        with pytest.raises(TaskError):
            cancel(admission)
        assert admission.queue.get(task.id) == reserved
    else:
        retired = admission.queue.settle_standing_retirement(
            task.id, expected_revision=task.standing_revision,
        )
        assert retired.standing_charter["lifecycle"] == "retired" and retired.owner is None


@pytest.mark.parametrize("changes", [
    {"expected_revision": True}, {"expected_generation": True},
    {"expected_revision": 999}, {"expected_generation": 999}, {"expected_generation": 0},
    {"workspace": "another-creation"},
])
def test_cancel_refuses_stale_or_ambiguous_fences_without_effects(admission, changes):
    if "workspace" in changes:
        changes = {"workspace": replace(admission.workspace, creation_nonce=changes["workspace"])}
    events = admission.queue.events(admission.task.id)
    relationship = admission.queue.task_relationship(admission.task.id)
    with pytest.raises(TaskError):
        cancel(admission, **changes)
    assert admission.queue.get(admission.task.id) == admission.task
    assert admission.queue.events(admission.task.id) == events
    assert admission.queue.task_relationship(admission.task.id) == relationship
    assert admission.owner.claims[admission.request["request_id"]]["phase"] == "bound"


def test_cancel_refuses_an_accepted_attachment(admission):
    admission.task = accept(admission)
    events = admission.queue.events(admission.task.id)
    with pytest.raises(TaskError, match="unaccepted reservation"):
        cancel(admission)
    assert admission.queue.get(admission.task.id) == admission.task
    assert admission.queue.events(admission.task.id) == events
    assert admission.owner.claims[admission.request["request_id"]]["phase"] == "bound"


def test_cancel_settles_retired_reservation_whose_binding_reply_was_lost(admission):
    task = cancel(admission)
    queue = TaskQueue(
        admission.queue.db_path, relationship_writer=admission.owner, relationship_authority="local",
    )
    admission.owner.queue = queue
    admission.owner.lose_reply = "bind"
    with pytest.raises(TaskError, match="unresolved"):
        queue.reserve_standing_charter(
            task.id, admission.workspace, expected_revision=task.standing_revision,
        )
    pending = queue.get(task.id)
    request = queue.task_relationship(task.id)["request"]
    assert pending.status == "claimed" and queue.task_relationship(task.id)["state"] == "pending"
    retiring = queue.retire_standing_charter(task.id, expected_revision=pending.standing_revision)
    with pytest.raises(TaskError, match="not open"):
        queue.reserve_standing_charter(
            task.id, admission.workspace, expected_revision=task.standing_revision,
        )
    cancelled = queue.cancel_standing_reservation(
        task.id, admission.workspace,
        expected_revision=retiring.standing_revision, expected_generation=retiring.generation,
    )
    assert cancelled.status == "queued" and cancelled.owner is None
    assert queue.task_relationship(task.id)["state"] == "closed"
    assert admission.owner.claims[request["request_id"]]["phase"] == "closed"
    retired = queue.settle_standing_retirement(task.id, expected_revision=cancelled.standing_revision)
    assert retired.standing_charter["lifecycle"] == "retired"


@pytest.mark.parametrize("succeeding", [False, True])
def test_acceptance_replays_after_commit_reply_loss_restart_and_later_denial(
    admission, monkeypatch, succeeding,
):
    arguments = prepare_successor(admission) if succeeding else {}
    before_events = admission.queue.events(admission.task.id)

    def lose_reply():
        raise TimeoutError("acceptance committed; response lost")

    monkeypatch.setattr(admission.queue, "_notify_owned_transition", lose_reply)
    with pytest.raises(TimeoutError, match="response lost"):
        accept(admission, **arguments)
    accepted = admission.queue.get(admission.task.id)
    assert accepted.status == "started" and accepted.owner_session_id == admission.head["session_id"]
    assert accepted.standing_revision == admission.task.standing_revision + 1
    assert accepted.generation == admission.task.generation + int(succeeding)
    assert accepted.lease_expires_at is None
    assert accepted.standing_charter["accepted_materialization"]["head"] == admission.head
    assert len(admission.queue.events(admission.task.id)) == len(before_events) + 1
    attachments = admission.queue.attachment_history(admission.task.id)
    assert len(attachments) == 1 + int(succeeding)
    active = [attachment for attachment in attachments if attachment.detached_at is None]
    assert len(active) == 1
    assert active[0].session_id == admission.head["session_id"]
    assert active[0].worktree_id == admission.workspace.worktree
    assert active[0].machine == admission.workspace.machine
    assert {operation for operation, _ in admission.owner.calls[-2:]} == {"inspect"}
    set_standing_attachment_deny(
        admission.config.overrides_path,
        StandingAttachmentSelector("operator", "dispatcher", "primary"), reason="no new attachments",
    )
    admission.queue = TaskQueue(admission.queue.db_path)
    calls = len(admission.calls)
    assert accept(admission, **arguments) == accepted
    assert len(admission.calls) == calls
    assert admission.queue.attachment_history(admission.task.id) == attachments
    assert len(admission.queue.events(admission.task.id)) == len(before_events) + 1
    with pytest.raises(TaskError, match="revision changed"):
        accept(admission, **{**arguments, "owner_session_id": "unaccepted-successor"})
    assert admission.queue.get(admission.task.id) == accepted


@pytest.mark.parametrize("succeeding", [False, True])
def test_concurrent_same_acceptance_records_one_native_attachment(admission, monkeypatch, succeeding):
    arguments = prepare_successor(admission) if succeeding else {}
    barrier = Barrier(2)
    native = admission.writer.admit_standing

    @contextmanager
    def raced(*args):
        barrier.wait(timeout=5)
        with native(*args) as value:
            yield value

    monkeypatch.setattr(admission.writer, "admit_standing", raced)
    before_events = admission.queue.events(admission.task.id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: accept(admission, **arguments), range(2)))
    assert results[0] == results[1]
    assert len(admission.queue.attachment_history(admission.task.id)) == 1 + int(succeeding)
    assert len(admission.queue.events(admission.task.id)) == len(before_events) + 1


def test_successive_heads_continue_the_original_assignment_without_reserving_again(admission):
    arguments = prepare_successor(admission)
    original = admission.queue.task_relationship(admission.task.id)
    calls = len(admission.owner.calls)
    first = accept(admission, **arguments)
    admission.task = first
    admission.head = {
        "session_id": "third-native-session", "revision": 3,
        "predecessor_session_id": first.owner_session_id,
    }
    second = accept(admission, expected_owner_session_id=first.owner_session_id)
    relationship = admission.queue.task_relationship(second.id)
    assert relationship["request"] == original["request"]
    assert relationship["receipt"] == original["receipt"]
    assert relationship["continuation"] == {
        "generation": second.generation, "owner_session_id": second.owner_session_id,
    }
    assert second.generation == original["request"]["generation"] + 2
    accepted = second.standing_charter["accepted_materialization"]
    assert accepted["assignment_generation"] == original["request"]["generation"]
    assert accepted["generation"] == second.generation
    assert accepted["expected_generation"] == first.generation
    assert second.owner == first.owner == admission.workspace.worker_id
    assert second.attempts == first.attempts == 1
    assert {operation for operation, _ in admission.owner.calls[calls:]} == {"inspect"}
    history = admission.queue.attachment_history(second.id)
    assert len(history) == 3
    assert [item.session_id for item in history if item.detached_at is None] == [second.owner_session_id]
    assert all(item.detach_reason == "standing succession" for item in history if item.detached_at)


def change_materialization(admission, materialization, objective="Changed reviewer objective."):
    """Rematerialize the declared role and reconcile its new revisions onto the attached lane."""
    command, _ = materialization
    skill = Path(command[1]).parents[1]
    topology = json.loads((skill / "assets" / "topology.example.json").read_text(encoding="utf-8"))
    topology["assignments"][0]["charter"]["objective"] = objective
    source = admission.path.with_name("changed-topology.json")
    source.write_text(json.dumps(topology), encoding="utf-8")
    result = module.run_background_capture(
        [*command, "materialize", str(source), "--request", topology["assignments"][0]["requestId"]],
        input_text="", timeout=15,
    )
    assert result is not None and result.returncode == 0, result
    document = json.loads(result.stdout)
    declaration = replace(
        admission.declaration, source_revision=document["sourceRevision"],
        expected_materialization_revision=document["materializationRevision"],
    )
    assert declaration != admission.declaration
    admission.task = admission.queue.reconcile_standing_charter(
        "Dispatcher", declaration, repo="owner/repo",
        expected_revision=admission.task.standing_revision,
    )
    return document, declaration


def test_same_head_reacceptance_restores_coverage_without_new_ownership(admission, materialization):
    accepted = accept(admission)
    admission.task = accepted
    relationship = admission.queue.task_relationship(accepted.id)
    attachments = admission.queue.attachment_history(accepted.id)
    document, declaration = change_materialization(admission, materialization)
    stale = admission.task.standing_charter["accepted_materialization"]
    assert stale["source_revision"] != declaration.source_revision
    admission.path.write_text(json.dumps(document), encoding="utf-8")
    calls = len(admission.owner.calls)
    admission.head = {**admission.head, "revision": 2}
    reaccepted = accept(admission)
    covered = reaccepted.standing_charter["accepted_materialization"]
    assert covered["source_revision"] == declaration.source_revision
    assert covered["materialization_revision"] == declaration.expected_materialization_revision
    assert covered["head"] == admission.head and covered["expected_revision"] == admission.task.standing_revision
    assert covered["generation"] == covered["expected_generation"] == accepted.generation
    assert reaccepted.generation == accepted.generation and reaccepted.owner == accepted.owner
    assert reaccepted.owner_session_id == accepted.owner_session_id
    assert reaccepted.started_at == accepted.started_at and reaccepted.attempts == accepted.attempts
    assert admission.queue.task_relationship(accepted.id) == relationship
    assert admission.queue.attachment_history(accepted.id) == attachments
    assert {operation for operation, _ in admission.owner.calls[calls:]} == {"inspect"}
    assert accept(admission) == reaccepted
    admission.task = reaccepted
    admission.conclusion = {"session_id": admission.head["session_id"], "revision": 3}
    released = release(admission)
    assert released.owner is None and released.generation == accepted.generation + 1


@pytest.mark.parametrize("change", [
    "current", "stale-materialization", "regressed-head", "another-session", "retiring",
])
def test_reacceptance_requires_changed_coverage_from_the_same_admitted_head(
    admission, materialization, change,
):
    admission.head = {**admission.head, "revision": 2}
    admission.task = accept(admission)
    changes = {}
    if change != "current":
        document, _ = change_materialization(admission, materialization)
        if change != "stale-materialization":
            admission.path.write_text(json.dumps(document), encoding="utf-8")
    if change == "regressed-head":
        admission.head = {**admission.head, "revision": 1}
    elif change == "another-session":
        admission.head = {"session_id": "another-native-session", "revision": 3}
    elif change == "retiring":
        admission.task = admission.queue.retire_standing_charter(
            admission.task.id, expected_revision=admission.task.standing_revision,
        )
    before_events = admission.queue.events(admission.task.id)
    before_relationship = admission.queue.task_relationship(admission.task.id)
    with pytest.raises(TaskError, match={
        "current": "already covers", "stale-materialization": "materialization",
        "regressed-head": "previously accepted native head", "another-session": "native attachment",
        "retiring": "not open",
    }[change]):
        accept(admission, **changes)
    assert admission.queue.get(admission.task.id) == admission.task
    assert admission.queue.events(admission.task.id) == before_events
    assert admission.queue.task_relationship(admission.task.id) == before_relationship


@pytest.mark.parametrize("changes", [
    {"expected_owner_session_id": None}, {"expected_owner_session_id": ""},
    {"expected_owner_session_id": True}, {"expected_owner_session_id": "another-owner"},
    {"expected_owner_session_id": "native-successor"}, {"expected_owner_session_id": " native-session"},
    {"expected_revision": 999}, {"expected_generation": 999}, {"expected_head_revision": 1},
    {"expected_head_revision": True}, {"owner_session_id": "another-successor"},
])
def test_succession_rejects_stale_or_ambiguous_previous_owners(admission, changes):
    arguments = prepare_successor(admission)
    before_events = admission.queue.events(admission.task.id)
    before_relationship = admission.queue.task_relationship(admission.task.id)
    before_attachments = admission.queue.attachment_history(admission.task.id)
    with pytest.raises(TaskError):
        accept(admission, **{**arguments, **changes})
    assert admission.queue.get(admission.task.id) == admission.task
    assert admission.queue.events(admission.task.id) == before_events
    assert admission.queue.task_relationship(admission.task.id) == before_relationship
    assert admission.queue.attachment_history(admission.task.id) == before_attachments


@pytest.mark.parametrize("boundary", ["resident", "direct"])
@pytest.mark.parametrize("change", ["missing", "different", "same", "blank"])
def test_succession_requires_the_confirmed_predecessor_from_both_owners(admission, boundary, change):
    arguments = prepare_successor(admission)
    calls = len(admission.calls)

    def mutate(argv, result):
        if ("--resident-only" in argv) != (boundary == "resident"):
            return
        if change == "missing":
            result["head"].pop("predecessor_session_id")
        else:
            result["head"]["predecessor_session_id"] = {
                "different": "other-owner", "same": admission.head["session_id"], "blank": "",
            }[change]

    admission.mutate = mutate
    with pytest.raises(TaskError, match="unconfirmed|did not confirm"):
        accept(admission, **arguments)
    assert admission.queue.get(admission.task.id) == admission.task
    assert "continuation" not in admission.queue.task_relationship(admission.task.id)
    if boundary == "resident":
        assert not any("record" in call for call in admission.calls[calls:])


@pytest.mark.parametrize("capability", [None, True, 0, 2])
def test_old_writer_refuses_succession_without_losing_ordinary_inspection(admission, capability):
    arguments = prepare_successor(admission)
    admission.support["succession_inspect"] = capability
    writer = module.WorktreeRelationshipWriter(
        ["selected-worktrees"], admit_workspace=lambda *_: None,
        admit_request=lambda *_: None, standing=admission.config, require_resident=True,
    )
    admission.queue = TaskQueue(
        admission.queue.db_path, relationship_writer=writer, relationship_authority="local",
    )
    assert writer.bind_request(admission.request)(
        admission.workspace, "inspect",
        {"request_id": admission.request["request_id"], "allow_missing": True},
    )["phase"] == "bound"
    calls = len(admission.owner.calls)
    with pytest.raises(TaskError, match="cannot confirm native succession"):
        accept(admission, **arguments)
    assert len(admission.owner.calls) == calls
    assert admission.queue.get(admission.task.id) == admission.task


@pytest.mark.parametrize("changes", [
    {"expected_revision": True}, {"expected_generation": True}, {"expected_head_revision": True},
    {"expected_revision": 999}, {"expected_generation": 999}, {"expected_head_revision": 999},
    {"owner_session_id": "unconfirmed-head"}, {"owner_session_id": ""},
])
def test_acceptance_rejects_stale_or_ambiguous_fences_without_queue_mutation(admission, changes):
    before_events = admission.queue.events(admission.task.id)
    with pytest.raises(TaskError):
        accept(admission, **changes)
    assert admission.queue.get(admission.task.id) == admission.task
    assert admission.queue.events(admission.task.id) == before_events
    assert admission.queue.attachment_history(admission.task.id) == []


@pytest.mark.parametrize("change", ["hold", "declaration", "retirement", "relationship", "worker"])
@pytest.mark.parametrize("succeeding", [False, True])
def test_queue_rechecks_admission_after_native_reads(admission, monkeypatch, change, succeeding):
    arguments = prepare_successor(admission) if succeeding else {}
    before_attachments = admission.queue.attachment_history(admission.task.id)
    def mutate(argv, result):
        if "--resident-only" in argv:
            return
        admission.mutate = None
        if change == "hold":
            admission.queue.set_hold(admission.task.id, reason="concurrent hold", actor="operator")
        elif change == "declaration":
            admission.queue.reconcile_standing_charter(
                "Dispatcher", replace(admission.declaration, source_revision="new-source"),
                repo=admission.task.repo, expected_revision=admission.task.standing_revision,
            )
        elif change == "retirement":
            admission.queue.retire_standing_charter(
                admission.task.id, expected_revision=admission.task.standing_revision,
            )
        elif change == "relationship":
            with admission.queue._connect() as conn:
                conn.execute(
                    "UPDATE task_events SET task_relationship=json_set(task_relationship, '$.state', 'pending')"
                    " WHERE id=?", (admission.request["event_id"],),
                )
        else:
            def refuse():
                raise TaskError("worker admission withdrawn")

            monkeypatch.setattr(admission.queue, "require_worker_admission", refuse)

    admission.mutate = mutate
    with pytest.raises(TaskError, match="held|revision changed|original relationship|admission withdrawn"):
        accept(admission, **arguments)
    current = admission.queue.get(admission.task.id)
    assert current.status == admission.task.status
    assert current.owner_session_id == admission.task.owner_session_id
    assert current.generation == admission.task.generation
    assert current.standing_charter["accepted_materialization"] == (
        admission.task.standing_charter["accepted_materialization"]
    )
    assert admission.queue.attachment_history(admission.task.id) == before_attachments


@pytest.mark.parametrize("succeeding", [False, True])
def test_acceptance_rollback_covers_task_audit_and_attachment(admission, monkeypatch, succeeding):
    arguments = prepare_successor(admission) if succeeding else {}
    record = admission.queue._record_attachment
    before_events = admission.queue.events(admission.task.id)
    before_relationship = admission.queue.task_relationship(admission.task.id)
    before_attachments = admission.queue.attachment_history(admission.task.id)

    def fail_after_record(*args, **kwargs):
        record(*args, **kwargs)
        raise OSError("simulated storage error")

    monkeypatch.setattr(admission.queue, "_record_attachment", fail_after_record)
    with pytest.raises(TaskError, match="simulated storage error"):
        accept(admission, **arguments)
    assert admission.queue.get(admission.task.id) == admission.task
    assert admission.queue.events(admission.task.id) == before_events
    assert admission.queue.task_relationship(admission.task.id) == before_relationship
    assert admission.queue.attachment_history(admission.task.id) == before_attachments
    monkeypatch.setattr(admission.queue, "_record_attachment", record)
    assert accept(admission, **arguments).status == "started"
    assert len(admission.queue.attachment_history(admission.task.id)) == 1 + int(succeeding)


def test_recreated_native_relationship_cannot_accept_original_reservation(admission):
    admission.owner.claims[admission.request["request_id"]]["token"] = "r" * 43
    with pytest.raises(TaskError, match="original reserved assignment"):
        accept(admission)
    assert admission.queue.get(admission.task.id) == admission.task
    assert admission.queue.attachment_history(admission.task.id) == []


def test_unqualified_provider_cannot_accept_from_a_retained_receipt(admission):
    admission.queue = TaskQueue(
        admission.queue.db_path, relationship_writer=admission.owner, relationship_authority="local",
    )
    calls = len(admission.owner.calls)
    with pytest.raises(TaskError, match="cannot admit a standing attachment"):
        accept(admission)
    assert len(admission.owner.calls) == calls
    assert admission.queue.get(admission.task.id) == admission.task


@pytest.mark.parametrize("capability", [None, True, 0, 2])
def test_missing_checked_head_capability_keeps_finite_assignment_reads_usable(admission, capability):
    admission.support["head_inspect"] = capability
    writer = module.WorktreeRelationshipWriter(
        ["selected-worktrees"], admit_workspace=lambda *_: None,
        admit_request=lambda *_: None, standing=admission.config, require_resident=True,
    )
    bound = writer.bind_request(admission.request)
    assert bound(
        admission.workspace, "inspect",
        {"request_id": admission.request["request_id"], "allow_missing": True},
    )["phase"] == "bound"
    with pytest.raises(TaskError, match="exact native head"):
        writer.inspect_head(admission.request, admission.head)
    with pytest.raises(TaskError, match="unavailable"):
        with writer.admit_standing(admission.request, asdict(admission.declaration), admission.head):
            pytest.fail("unqualified owner admitted a standing attachment")


@pytest.mark.parametrize("missing", ["policy", "head_inspect", "conclusion_inspect"])
def test_attachment_support_requires_policy_and_native_inspection(admission, missing):
    from agent_dispatch.coordinator_standing import standing_capability

    assert admission.queue.standing_attachment_supported()
    if missing != "policy":
        admission.support[missing] = None
    writer = module.WorktreeRelationshipWriter(
        ["selected-worktrees"], admit_workspace=lambda *_: None, admit_request=lambda *_: None,
        standing=None if missing == "policy" else admission.config, require_resident=True,
    )
    queue = TaskQueue(
        admission.queue.db_path, relationship_writer=writer, relationship_authority="local",
    )
    assert standing_capability(queue)["attachment"] is False
    lane = queue.reconcile_standing_charter(
        "Secondary", replace(admission.declaration, lane_key="secondary"), repo="owner/repo",
    )
    calls = len(admission.owner.calls)
    with pytest.raises(TaskError, match="cannot admit a standing attachment"):
        queue.reserve_standing_charter(
            lane.id, admission.workspace, expected_revision=lane.standing_revision,
        )
    assert queue.get(lane.id) == lane and len(admission.owner.calls) == calls


@pytest.mark.parametrize("change", ["artifact", "source-revision", "effective-revision", "foreign-host"])
def test_materialization_drift_and_foreign_host_policy_cannot_reach_native_acceptance(admission, change):
    declaration = admission.declaration
    if change == "artifact":
        document = json.loads(admission.path.read_text(encoding="utf-8"))
        document["effective"]["charter"]["objective"] = "Unreviewed replacement"
        admission.path.write_text(json.dumps(document), encoding="utf-8")
    elif change == "source-revision":
        declaration = replace(declaration, source_revision="changed-source")
    elif change == "effective-revision":
        declaration = replace(declaration, expected_materialization_revision="changed-effective")
    else:
        admission.writer._standing = replace(admission.config, machine="another-host")
    calls = len(admission.calls)
    with pytest.raises(TaskError, match="materialization|host-local policy"):
        with admission.writer.admit_standing(admission.request, asdict(declaration), admission.head):
            pytest.fail("drifted declaration was admitted")
    assert all(call[0] != "selected-worktrees" for call in admission.calls[calls:])


def test_local_deny_precedes_reads_and_is_serialized_through_acceptance_commit(admission):
    selector = StandingAttachmentSelector(
        admission.declaration.namespace, admission.declaration.charter_key, admission.declaration.lane_key,
    )
    entered = Event()

    def deny():
        entered.set()
        return set_standing_attachment_deny(
            admission.config.overrides_path, selector, reason="operator veto",
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        with admission.writer.admit_standing(
            admission.request, asdict(admission.declaration), admission.head,
        ):
            pending = executor.submit(deny)
            assert entered.wait(5)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.05)
        assert pending.result(timeout=5)["disabled"] is True
    calls = len(admission.calls)
    with pytest.raises(TaskError, match="denied locally: operator veto"):
        with admission.writer.admit_standing(
            admission.request, asdict(admission.declaration), admission.head,
        ):
            pytest.fail("denied attachment was admitted")
    assert len(admission.calls) == calls
