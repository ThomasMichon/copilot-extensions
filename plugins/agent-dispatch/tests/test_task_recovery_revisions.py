"""Operation-specific recovery revisions preserve semantic state, not liveness clocks."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.queue_execution_wakes import wake_table
from agent_dispatch.queue_records import TaskError
from agent_dispatch.task_wake_recovery import reconcile_wake
from tests import test_task_wake_recovery as recovery
from tests.test_native_owner_wake import NATIVE, OWNER
from tests.test_native_owner_wake import hosted as hosted
from tests.test_task_scope_auth import CONTROL_TOKEN, TOKEN, _auth
from tests.test_task_wake_recovery import retained as retained
from tests.test_task_wake_settlement import negative

current = cast(Callable[..., Any], recovery.current)
receipt = cast(Callable[..., dict[str, Any]], recovery.receipt)


@pytest.mark.parametrize("retained", ["owner"], indirect=True)
@pytest.mark.parametrize("provider", ["passive", "accepted", "not_admitted"])
def test_reconciliation_revision_survives_real_owner_heartbeats(
    retained: Any, provider: str,
) -> None:
    queue, task_id, wake = retained
    before = queue.get(task_id)
    diagnostic = queue.task_diagnostics(task_id)
    queue.heartbeat(task_id, OWNER, now=before.updated_at + 1)
    newer = queue.task_diagnostics(task_id)
    assert newer["revision"] != diagnostic["revision"]
    assert newer["reconciliation_revision"] == diagnostic["reconciliation_revision"]
    calls = []

    def observe(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(True)
        queue.heartbeat(task_id, OWNER, now=before.updated_at + 2)
        queue.set_activity(
            task_id, "ACTIVE", reservation_key=queue.latest_reservation(task_id).key,
            now=before.updated_at + 2,
        )
        return (
            negative(wake) if provider == "not_admitted"
            else {**receipt(wake), "outcome": "accepted"}
        )

    result = reconcile_wake(
        queue, task_id, outbox=wake_table(wake), wake_id=wake.id,
        expected_revision=diagnostic["reconciliation_revision"],
        actor="operator", reason="reconcile the original live owner",
        settle_native_send=provider != "passive", observe=observe,
    )
    assert result["outcome"] == ("not_sent" if provider == "not_admitted" else "sent")
    assert calls == [True]
    after = queue.get(task_id)
    assert (after.generation, after.owner_session_id) == (before.generation, NATIVE)
    assert after.lease_expires_at == before.updated_at + 2 + queue.lease_seconds
    assert current(queue, task_id, wake).attempts == wake.attempts


@pytest.mark.parametrize("when", ["before", "during"])
@pytest.mark.parametrize(("statement", "value"), [
    ("UPDATE tasks SET payload_inline=? WHERE id=?", '{"changed":"payload"}'),
    ("UPDATE tasks SET target_worktree=? WHERE id=?", "different-workspace"),
    ("UPDATE tasks SET hold_reason=? WHERE id=?", "independent hold"),
    ("UPDATE tasks SET card_draft=? WHERE id=?", '{"question":"changed"}'),
])
def test_wake_revision_keeps_semantic_changes_without_a_timestamp_update_fenced(
    retained: Any, when: str, statement: str, value: str,
) -> None:
    queue, task_id, wake = retained
    diagnostic = queue.task_diagnostics(task_id)
    updated_at = queue.get(task_id).updated_at
    calls = []

    def change() -> None:
        with queue._connect() as conn:
            conn.execute(statement, (value, task_id))
        assert queue.get(task_id).updated_at == updated_at

    def observe(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(True)
        if when == "during":
            change()
        return {**receipt(wake), "outcome": "accepted"}

    if when == "before":
        change()
    with pytest.raises(TaskError, match="changed"):
        reconcile_wake(
            queue, task_id, outbox=wake_table(wake), wake_id=wake.id,
            expected_revision=diagnostic["reconciliation_revision"],
            actor="operator", reason="semantic task change must invalidate the snapshot",
            settle_native_send=True, observe=observe,
        )
    assert calls == ([True] if when == "during" else [])
    assert current(queue, task_id, wake).execution_send_state == "unknown"


def test_wake_revision_cannot_authorize_lifecycle_recovery(retained: Any) -> None:
    queue, task_id, wake = retained
    diagnostic = queue.task_diagnostics(task_id)
    with pytest.raises(TaskError, match="changed"):
        queue.recover_task(
            task_id, operation="reset_proposed",
            expected_revision=diagnostic["reconciliation_revision"],
            request_id="different-operation", actor="operator", reason="must refuse",
        )
    assert current(queue, task_id, wake).execution_send_state == "unknown"
    assert "_reconciliation_state" not in json.dumps(diagnostic)


@pytest.mark.parametrize("retained", ["owner"], indirect=True)
@pytest.mark.parametrize("selected", ["revision", "reconciliation_revision"])
def test_actual_http_revision_selection_keeps_legacy_and_wake_fences_distinct(
    retained: Any, selected: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id, wake = retained
    observed = []

    def observe(*args: Any, **kwargs: Any) -> dict[str, Any]:
        observed.append(True)
        task = queue.get(task_id)
        queue.heartbeat(task_id, OWNER, now=task.updated_at + 1)
        return {**receipt(wake), "outcome": "accepted"}

    monkeypatch.setattr("agent_dispatch.task_wake_recovery.native_prompt_settlement", observe)
    with TestClient(create_app(queue, token=TOKEN, control_token=CONTROL_TOKEN)) as api:
        diagnostics = api.get(
            f"/tasks/{task_id}/diagnostics", headers=_auth(TOKEN),
        ).json()
        task = queue.get(task_id)
        queue.heartbeat(task_id, OWNER, now=task.updated_at + 1)
        response = api.post(
            f"/tasks/{task_id}/wakes/reconcile", headers=_auth(CONTROL_TOKEN),
            json={
                "outbox": wake_table(wake), "wake_id": wake.id,
                "expected_revision": diagnostics[selected], "actor": "operator",
                "reason": "original owner remains live", "settle_native_send": True,
            },
        )
    if selected == "revision":
        assert response.status_code == 409
        assert observed == []
        assert current(queue, task_id, wake).execution_send_state == "unknown"
    else:
        assert response.status_code == 200
        assert observed == [True]
        assert response.json()["outcome"] == "sent"
