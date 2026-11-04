"""Both source owners share real ledger custody across an interrupted assignment."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import claim_providers, finalize, tracking
from agent_worktrees.tracking_relationship_write import apply_resource_relationship
from test_resource_producer import owner as owner


@pytest.mark.parametrize("lost_reply", ["reserve", "bind"])
@pytest.mark.parametrize("disposition", ["unassigned", "completed"])
def test_real_dispatch_assignment_blocks_cleanup_until_exact_disposition(
    owner, monkeypatch, tmp_path, lost_reply, disposition,
):
    source = Path(__file__).resolve().parents[2] / "agent-dispatch" / "src"
    monkeypatch.syspath_prepend(str(source))
    from agent_dispatch.assignment_workspace import AssignmentWorkspace
    from agent_dispatch.queue import TaskError, TaskQueue
    from agent_dispatch.task_obligations import read_snapshot
    from agent_dispatch.task_relationships import AssignmentSubject

    path, _ = owner
    record = tracking.load_record(path)
    workspace = AssignmentWorkspace("m", "p", "owner", record.worktree_path, record.creation_nonce)
    lose = [lost_reply]

    def writer(workspace, operation, payload):
        result = apply_resource_relationship({
            "schema": "copilot-extensions.resource-relationship", "version": 1,
            "yaml_path": str(path), "owner_ref": workspace.owner_ref,
            "operation": operation, "input": payload,
        })
        assert result.get("ok"), result
        if lose and operation == lose[0]:
            lose.clear()
            raise TimeoutError("original owner committed; response lost")
        return result

    queue = TaskQueue(
        tmp_path / "queue.db", relationship_writer=writer, relationship_authority="local",
    )

    def provider(ref):
        subject = AssignmentSubject.decode(ref.split(":", 1)[1], authority="local")
        return {"available": True, **subject.verification({
            "task_obligation": read_snapshot(
                queue, subject.task_id, assignment_event_id=subject.assignment_event_id,
            ),
        })}

    monkeypatch.setattr(claim_providers, "resolve_claim_status", provider)
    task = queue.create("work", repo="p", require_verification=True)
    with pytest.raises(TaskError, match="unresolved"):
        queue.claim_one(workspace.worker_id, task_id=task.id, assignment_workspace=workspace)
    record = tracking.load_record(path)
    assert len(record.resources) == 1 and record.resources[0].is_unsettled
    assert not finalize._assert_obligations_settled(record, "owner", abandon=False)
    assert queue.task_relationship(task.id)["state"] == "pending"
    if disposition == "unassigned":
        queue.yield_task(task.id, workspace.worker_id)
    else:
        queue.claim_one(workspace.worker_id, task_id=task.id, assignment_workspace=workspace)
        queue.start(task.id, workspace.worker_id, owner_session_id="native")
        queue.complete(task.id, workspace.worker_id, result={"answer": "proposed"})
        assert tracking.load_record(path).resources[0].is_unsettled
        assert not provider(tracking.load_record(path).resources[0].ref)["relationship_verification"]["retired"]
        queue.confirm(task.id)
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"
    record = tracking.load_record(path)
    assert len(record.resources) == 1 and not record.resources[0].is_unsettled
    assert record.resources[0].producer["phase"] == "closed"
    assert finalize._assert_obligations_settled(record, "owner", abandon=False)
