"""Original writer failures stay observable without releasing unknown effects."""

from __future__ import annotations

import json
import logging

import pytest

from agent_dispatch.queue import TaskError
from agent_dispatch.assignment_relationship_owner import _refusal_detail
from tests.test_assignment_relationships import assignment as assignment, claim


@pytest.mark.parametrize("operation", ["reserve", "bind"])
def test_original_writer_cause_is_redacted_and_unresolved_request_is_unchanged(
    assignment: tuple, operation: str, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue, owner, workspace, task = assignment
    secret = "synthetic-relationship-diagnostic-secret"
    monkeypatch.setenv("DISPATCH_RELATIONSHIP_TEST_SECRET", secret)
    caplog.set_level(logging.INFO, logger="agent-dispatch.spawn")

    def refuse(selected: str, _result: dict) -> None:
        if selected == operation:
            try:
                raise ValueError("selected record admission refused " + secret)
            except ValueError as cause:
                raise RuntimeError("original writer unavailable") from cause

    owner.before_reply = refuse
    with pytest.raises(TaskError) as failure:
        claim(queue, workspace, task)
    assert str(failure.value) == (
        "task relationship request is unresolved; reconcile its original assignment"
    )
    relationship = queue.task_relationship(task.id)
    assert relationship["state"] == "pending"
    assert queue.get(task.id).owner == workspace.worker_id
    assert len(owner.claims) == 1
    with pytest.raises(TaskError, match="unconfirmed"):
        queue.start(task.id, workspace.worker_id)
    [record] = [
        json.loads(item.message) for item in caplog.records
        if item.name == "agent-dispatch.spawn"
    ]
    assert record["stage"] == "task_relationship_write"
    assert record["outcome"] == "unresolved" and record["operation"] == operation
    assert record["task_id"] == task.id
    assert record["assignment_event_id"] == relationship["request"]["event_id"]
    assert record["generation"] == relationship["request"]["generation"]
    assert record["error_type"] == "RuntimeError"
    assert "original writer unavailable" in record["error"]
    assert "ValueError: selected record admission refused [REDACTED]" in record["error"]
    assert secret not in caplog.text
    assert "token" not in record and "payload" not in record


@pytest.mark.parametrize("carrier", ["structured", "stderr"])
def test_selected_owner_refusal_redacts_before_cutoff_and_causal_event(
    assignment: tuple, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, carrier: str,
) -> None:
    queue, owner, workspace, task = assignment
    secret = "sensitive-boundary-value-" + "x" * 600
    monkeypatch.setenv("DISPATCH_RELATIONSHIP_TEST_SECRET", secret)
    caplog.set_level(logging.INFO, logger="agent-dispatch.spawn")
    text = "selected owner refused " + "p" * 440 + " " + secret
    assert text.index(secret) < 512 < text.index(secret) + len(secret)

    def refuse(operation: str, _result: dict) -> None:
        detail = _refusal_detail(
            ["resource-relationship", operation],
            {"error": text} if carrier == "structured" else None,
            text if carrier == "stderr" else None,
        )
        raise TaskError("selected owner command failed" + detail)

    owner.before_reply = refuse
    with pytest.raises(TaskError, match="request is unresolved"):
        claim(queue, workspace, task)
    [record] = [
        json.loads(item.message) for item in caplog.records
        if item.name == "agent-dispatch.spawn"
    ]
    assert record["stage"] == "task_relationship_write"
    assert "selected owner refused" in record["error"]
    assert "[REDACTED]" in record["error"]
    assert "sensitive-boundary-value-" not in caplog.text
    assert queue.task_relationship(task.id)["state"] == "pending"
