"""A paused cold request keeps its place, not a lane or an execution receipt."""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from agent_dispatch.action_controls import require_worker_admission
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_pool_recovery import reconcile_recoveries
from agent_dispatch.queue_records import TaskError
from tests import test_ordinary_pool_startup as acp
from tests._hosting_owner import admission_owner as admission_owner
from tests.test_pool_placement import place, task
from tests.test_pool_suspended_recovery import _cold, _suspend_placed, suspended
from tests.test_scoped_worker_holds import command

ordinary, q, api = acp.ordinary, acp.q, acp.api


def test_held_cold_waiter_with_unknown_transport_still_occupies_slot(
    q: TaskQueue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(q, "_spawn_admission", require_worker_admission.require_spawn)
    held, key, generation = suspended(q)
    _cold(q, key)
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET labels='[\"paused\"]' WHERE id=?", (held,))
    command("hold", "--label", "paused", "--reason", "pause intake")
    q.resume(held, "host-a/worktree", wake_requested=True, now=110)
    with q._connect() as conn:
        conn.execute(
            "INSERT INTO wake_outbox(id,task_id,generation,wake_seq,owner,owner_session_id,status,attempts,"
            "created_at,updated_at,execution_send_state) VALUES('unknown-send',?,?,1,'host-a/worktree','native',"
            "'delivering',1,110,110,'unknown')", (held, generation),
        )
    before = q.list_wakes(held), q.get_reservation(key)
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    assert (q.list_wakes(held), q.get_reservation(key)) == before


@pytest.mark.parametrize(("scope", "late"), [(("--label", "paused"), False), (("--agent", "local"), True)])
@pytest.mark.parametrize("clear_first", [False, True])
def test_held_waiter_preserves_intent_without_consuming_sibling_lane(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, scope: tuple[str, str], late: bool, clear_first: bool,
) -> None:
    queue: TaskQueue = ordinary.q
    monkeypatch.setattr(queue, "_spawn_admission", require_worker_admission.require_spawn)
    held, key = ordinary.task["id"], ordinary.placed["reservation"]["key"]
    handle = acp.launch(ordinary)
    ordinary.client.record_spawn(key, session_handle=handle["session"], worktree=handle["worktree"],
                                 session_info=handle["session_info"], startup_complete=True, startup_revision=1)
    ordinary.client.start_pool_task(held, "host-a/worktree-a", owner_session_id="native-a", expected_generation=1)
    queue.suspend(held, "host-a/worktree-a", reason="park")
    queue.record_cold(key)
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET labels='[\"paused\"]' WHERE id=?", (held,))
    second, second_key = _suspend_placed(queue, "second")
    _cold(queue, second_key)
    running, _ = place(queue, task(queue))
    if not late:
        command("hold", *scope, "--reason", "pause intake")
    queue.resume(held, "host-a/worktree-a", wake_requested=True, wake_message="original intent", now=110)
    if late:
        command("hold", *scope, "--reason", "pause intake")
    before = queue.get(held), queue.get_reservation(key)
    fresh = task(queue)
    with pytest.raises(TaskError, match="capacity"):
        place(queue, fresh)  # real accountable reservation still occupies the single lane
    _cold(queue, running.key)
    admitted, reserved = place(queue, fresh)
    assert reserved and (queue.get(held), queue.get_reservation(key)) == before and not queue.list_wakes(held)
    queue.resume(second, "host-a/wt-second", wake_requested=True, now=111)
    _cold(queue, admitted.key)
    if clear_first:
        command("resume", *scope)
    assert reconcile_recoveries(queue, now=115) == 1
    winner, waiting = (held, second) if clear_first else (second, held)
    assert len(queue.list_wakes(winner)) == 1 and not queue.list_wakes(waiting)
    if not clear_first:
        command("resume", *scope)
    reconcile_recoveries(queue, now=116)
    assert len(queue.list_wakes(winner)) == 1 and not queue.list_wakes(waiting)
