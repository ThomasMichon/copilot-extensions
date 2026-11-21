from pathlib import Path
from typing import Any

import pytest

from agent_dispatch import gone_owner_recovery as recovery
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import TaskError
from agent_dispatch.queue_relevance import RelevanceScope


@pytest.mark.parametrize("interrupt", ["restart", "hold", "obsolete"])
def test_restart_finishes_only_the_original_eligible_incarnation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt: str,
) -> None:
    db = tmp_path / "queue.db"
    q = TaskQueue(db)
    task = q.create("work", repo="example", source="fixture", origin_ref="original")
    assert isinstance(task, Task)
    r, _ = q.reserve_spawn(task.id)
    r = q.record_spawn(r.key, startup_revision=1, startup_complete=True, session_handle="local-body:original")
    worker = "fixture/holder"
    q.claim_one(worker, repo="example", task_id=task.id)
    task = q.start(task.id, worker, owner_session_id="original-native")
    obs = recovery.Observation(supervisor="lane", key=r.key, generation=task.generation,
                               status="started", state="spawned", owner=worker, native=task.owner_session_id,
                               session_handle="local-body:original", worktree=r.worktree, startup_revision=1,
                               updated_at=r.updated_at, task_updated_at=task.updated_at,
                               wake_id=None, wake_status=None, productive=False)
    with pytest.raises(TaskError):
        recovery.begin(q, task.id, obs.model_copy(update={"generation": task.generation + 1}))
    old = q.get_reservation(r.key)
    assert old and old.state == "spawned"

    def crash(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulate coordinator exit after retirement commit")

    with monkeypatch.context() as patch:
        patch.setattr(recovery, "finish", crash)
        with pytest.raises(RuntimeError, match="after retirement"):
            recovery.begin(q, task.id, obs)
    replacement = TaskQueue(db)
    retired = replacement.get_reservation(r.key)
    assert retired and retired.state == "failed"
    counts = replacement.reconcile_liveness(resolver=lambda *_: "gone", headless_local_verdict=lambda _: "gone")
    assert counts["gone"] == 1 and counts["suspended"] == counts["requeued"] == 0
    still_owned = replacement.get(task.id)
    assert still_owned and still_owned.owner == worker
    assert recovery.reconcile(replacement, "other") == 0
    if interrupt == "obsolete":
        snapshot = replacement.relevance_snapshot(task.id)
        result = replacement.reconcile_task_relevance(
            task.id, request_id="closed", scope=RelevanceScope("example", "fixture", "original"),
            expected_generation=task.generation, expected_revision=snapshot["revision"],
            verdict="obsolete", reason="source closed", observation_ref="closed", reporter="source",
        )
        assert result["decision"] == "closeout_pending"
        assert recovery.reconcile(replacement, "lane") == 0
        retained = replacement.get(task.id)
        assert retained and retained.status == "started" and retained.owner == worker
        return
    if interrupt == "hold":
        replacement.set_hold(task.id, reason="inspect", actor="operator")
        assert recovery.reconcile(replacement, "lane") == 0
        replacement.clear_hold(task.id)
    assert recovery.reconcile(replacement, "lane") == 1
    assert recovery.reconcile(replacement, "lane") == 0
    queued = replacement.get(task.id)
    assert queued and queued.status == "queued" and queued.generation == obs.generation + 1
    assert replacement.reserve_spawn(task.id)[1]
