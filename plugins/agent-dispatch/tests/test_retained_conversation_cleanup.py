"""Failed attempt housekeeping keeps the original non-terminal conversation."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import pytest

from agent_dispatch import tracking
from agent_dispatch.queue_records import SpawnState, Status, TaskError
from agent_dispatch.supervisor import Supervisor
from tests.test_supervisor import client as client, q as q
from tests._helpers import TEST_REPO


def _released(queue: Any, *, driver: str = "ordinary-bridge") -> tuple[str, str]:
    task = queue.create("Retain original conversation")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(
        reservation.key, "retained-workspace", ownership="reused",
        creating_host="host-a", driver=driver,
    )
    queue.record_spawn(
        reservation.key, session_handle="local-body:original-handle",
        worktree="retained-workspace", startup_complete=True,
        startup_revision=queue.get_reservation(reservation.key).startup_revision,
    )
    queue.request_spawn_release(
        reservation.key, disposition="failed",
        finished_startup_revision=queue.get_reservation(reservation.key).startup_revision,
    )
    return task.id, reservation.key


def _retire(queue: Any, key: str) -> None:
    queue.retire_spawn(
        key, exact_absence=True, conclusion_state="pending",
        conclusion_detail=json.dumps({
            "action": "pending", "reason": "retired-body-cleanup", "cleanup_kind": "attempt",
            "session_end": {"state": "pending", "session_id": "original-handle"},
            "worktree_cleanup": {"state": "complete", "outcome": {"action": "preserved"}},
        }),
    )


def test_absent_body_releases_capacity_without_ending_retained_handle(q: Any, client: Any) -> None:
    task_id, key = _released(q)
    before = {
        key: value for key, value in asdict(q.get(task_id)).items()
        if key not in {"activity", "activity_updated_at"}
    }
    ended: list[str] = []

    def unused_spawn(_task: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        raise AssertionError("Cleanup cannot launch another body")

    def end(session_id: str) -> bool:
        ended.append(session_id)
        return True

    supervisor = Supervisor(
        client, spawn_fn=unused_spawn, repo=TEST_REPO, machine="host-a",
        local_body_verdict_fn=lambda _sid: tracking.GONE,
        local_acp_session_fn=lambda _sid: "original-native",
        local_end_fn=end, nudge=False,
    )

    assert supervisor.release_requested_bodies() == 1
    assert ended == []
    reservation = q.get_reservation(key)
    assert reservation.state == SpawnState.FAILED
    assert reservation.conversation_retired is False
    assert reservation.conclusion_state == "pending"
    assert q.get(task_id).status == Status.QUEUED
    assert {
        key: value for key, value in asdict(q.get(task_id)).items()
        if key not in {"activity", "activity_updated_at"}
    } == before
    assert supervisor.release_requested_bodies() == 0
    assert ended == []


@pytest.mark.parametrize("terminal", [False, True])
def test_cleanup_claim_tracks_current_task_need_without_rewriting_history(q: Any, terminal: bool) -> None:
    task_id, key = _released(q)
    _retire(q, key)
    if terminal:
        q.abandon(task_id, permitted=True, reason="Original task no longer needed")
    before_task, before_reservation = asdict(q.get(task_id)), asdict(q.get_reservation(key))
    reservation, claimed, token = q.claim_spawn_conclusion_retry(key, claimant_machine="host-a")
    assert claimed is terminal
    assert (token is not None) is terminal
    assert asdict(q.get(task_id)) == before_task
    if not terminal:
        assert asdict(reservation) == before_reservation
        assert asdict(q.get_reservation(key)) == before_reservation


def test_cleanup_checkpoint_cannot_authorize_end_of_nonterminal_conversation(q: Any) -> None:
    task_id, key = _released(q)
    before = asdict(q.get_reservation(key))
    with pytest.raises(TaskError, match="non-terminal conversation"):
        q.record_spawn_conclusion(
            key, conclusion_state="pending",
            conclusion_detail='{"action":"pending","reason":"ending-released-body"}',
        )
    assert asdict(q.get_reservation(key)) == before
    assert q.get(task_id).status == Status.QUEUED


@pytest.mark.parametrize("native", ["original-native", None], ids=["known-native", "unknown-native"])
def test_live_body_checkpoint_keeps_the_original_conversation(q: Any, native: str | None) -> None:
    from fastapi.testclient import TestClient
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.coordinator import create_app

    task_id, key = _released(q)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("A retained live conversation cannot be ended or replaced")

    with DispatchClient("http://testserver") as client:
        client._http.close()
        client._http = TestClient(create_app(q, enable_mcp=False, verification_interval=0))
        supervisor = Supervisor(
            client, spawn_fn=forbidden, repo=TEST_REPO, machine="host-a",
            local_body_verdict_fn=lambda _sid: tracking.LIVE,
            local_acp_session_fn=lambda _sid: native,
            local_end_fn=forbidden, nudge=False,
        )
        assert supervisor.release_requested_bodies() == 0
    assert q.get_reservation(key).state == SpawnState.RELEASING
    assert q.get(task_id).status == Status.QUEUED


def test_existing_cleanup_lease_cannot_outlive_renewed_task_need(q: Any) -> None:
    task_id, key = _released(q)
    _retire(q, key)
    q.abandon(task_id, permitted=True, reason="Fixture terminal transition")
    _, claimed, token = q.claim_spawn_conclusion_retry(key, claimant_machine="host-a")
    assert claimed and token
    with q._connect() as conn:
        conn.execute("UPDATE tasks SET status='queued' WHERE id=?", (task_id,))
    before = asdict(q.get_reservation(key))
    with pytest.raises(TaskError, match="non-terminal conversation"):
        q.validate_spawn_conclusion_claim(key, token, claimant_machine="host-a")
    with pytest.raises(TaskError, match="non-terminal conversation"):
        q.record_spawn_conclusion(
            key, conclusion_state="complete", conclusion_detail='{"action":"complete"}',
            claim_token=token, claimant_machine="host-a",
        )
    assert asdict(q.get_reservation(key)) == before


def test_legacy_nonordinary_cleanup_keeps_its_existing_admission(q: Any) -> None:
    _task_id, key = _released(q, driver="agent-dispatch")
    _retire(q, key)
    _reservation, claimed, token = q.claim_spawn_conclusion_retry(key, claimant_machine="host-a")
    assert claimed is True and token is not None
