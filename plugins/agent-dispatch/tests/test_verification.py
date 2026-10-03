from __future__ import annotations

import asyncio
import sys
import threading
import time

import pytest

from agent_dispatch import handoff_claim_release
from agent_dispatch import remote_dispatch
from agent_dispatch.queue import Status, TaskError
from agent_dispatch.verification import evaluate_submitted_task
from agent_dispatch.verification_drain import drain_verification_requests
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue


class _Bus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:
        self.events.append(event)


def _registration_machine() -> str:
    return remote_dispatch.local_machine() or "test-host"


def _submitted_task(
    queue: TaskQueue,
    title: str,
    *,
    require_verification: bool,
    evaluator_ref: str | None,
) -> str:
    task = queue.create(
        title,
        require_verification=require_verification,
        evaluator_ref=evaluator_ref,
    )
    queue.claim_one("worker-1", task_id=task.id)
    queue.start(task.id, "worker-1")
    queue.complete(task.id, "worker-1")
    return task.id


def _register_script(
    queue: TaskQueue,
    script_path: str,
    *,
    repo: str = TEST_REPO,
    env: str = "default",
) -> None:
    queue.register_registration(
        "evaluator",
        {
            "repo": repo,
            "evaluator_ref": "review-loop",
            "evaluator_spec": {
                "scripts": {"review-loop": [sys.executable, script_path]}
            },
        },
        machine=_registration_machine(),
        env=env,
    )


def test_evaluate_submitted_applies_confirm_abandon_and_noop(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "title = json.load(sys.stdin)['task']['title']\n"
        "if 'merged' in title:\n"
        "    decision = {'decision': 'confirm', 'reason': 'merged'}\n"
        "elif 'closed' in title:\n"
        "    decision = {'decision': 'abandon', 'reason': 'closed-unmerged'}\n"
        "else:\n"
        "    decision = {'decision': 'noop', 'reason': 'still-open'}\n"
        "json.dump(decision, sys.stdout)\n",
        encoding="utf-8",
    )
    _register_script(queue, str(script))

    merged_id = _submitted_task(
        queue,
        "target merged",
        require_verification=True,
        evaluator_ref="review-loop",
    )
    closed_id = _submitted_task(
        queue,
        "target closed",
        require_verification=True,
        evaluator_ref="review-loop",
    )
    waiting_id = _submitted_task(
        queue,
        "still waiting",
        require_verification=True,
        evaluator_ref="review-loop",
    )

    bus = _Bus()
    merged = evaluate_submitted_task(queue, merged_id, bus=bus, trigger="submitted")
    closed = evaluate_submitted_task(queue, closed_id, bus=bus, trigger="backfill")
    waiting = evaluate_submitted_task(queue, waiting_id, bus=bus, trigger="event-note")

    assert merged["applied"][0]["decision"] == "complete"
    assert closed["applied"][0]["decision"] == "abandon"
    assert waiting["applied"][0]["decision"] == "noop"
    assert queue.get(merged_id).status == Status.COMPLETED
    assert queue.get(closed_id).status == Status.ABANDONED
    assert queue.get(waiting_id).status == Status.SUBMITTED
    assert [event["type"] for event in bus.events] == [
        "task.completed",
        "task.abandoned",
    ]


def test_evaluate_submitted_respects_repo_scope_and_environment_fallback(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "tasks.db")
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "json.dump({'decision': 'confirm'}, sys.stdout)\n",
        encoding="utf-8",
    )
    _register_script(queue, str(script), env="staging")
    task_id = _submitted_task(
        queue,
        "staging task",
        require_verification=True,
        evaluator_ref="review-loop",
    )
    other_repo = "example.com/other/project"
    untouched = queue.create(
        "other repo",
        repo=other_repo,
        require_verification=True,
        evaluator_ref="review-loop",
    )
    queue.claim_one("worker-1", repo=other_repo, task_id=untouched.id)
    queue.start(untouched.id, "worker-1")
    queue.complete(untouched.id, "worker-1")

    monkeypatch.setenv("AGENT_DISPATCH_ENV", "staging")
    matched = evaluate_submitted_task(
        queue, task_id, trigger="submitted", current_machine=_registration_machine()
    )
    skipped = evaluate_submitted_task(
        queue, untouched.id, trigger="backfill", current_machine=_registration_machine()
    )

    assert matched["eligible"] is True
    assert queue.get(task_id).status == Status.COMPLETED
    assert skipped["eligible"] is False
    assert queue.get(untouched.id).status == Status.SUBMITTED


def test_evaluate_submitted_rejects_emit_decisions(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "json.dump({'decision': 'emit', 'title': 'follow-up', 'fields': {}}, sys.stdout)\n",
        encoding="utf-8",
    )
    _register_script(queue, str(script))
    task_id = _submitted_task(
        queue,
        "source task",
        require_verification=True,
        evaluator_ref="review-loop",
    )

    report = evaluate_submitted_task(queue, task_id, trigger="submitted")

    assert report["eligible"] is True
    assert "complete/abandon/noop" in report["reason"]
    assert queue.get(task_id).status == Status.SUBMITTED


@pytest.mark.parametrize(
    ("title", "expected_status"),
    [
        ("target merged", Status.COMPLETED),
        ("target closed", Status.ABANDONED),
    ],
)
def test_evaluate_submitted_releases_handoff_claims(tmp_path, monkeypatch, title, expected_status):
    queue = TaskQueue(tmp_path / "tasks.db")
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "title = json.load(sys.stdin)['task']['title']\n"
        "decision = {'decision': 'confirm'} if 'merged' in title else "
        "{'decision': 'abandon', 'reason': 'closed-unmerged'}\n"
        "json.dump(decision, sys.stdout)\n",
        encoding="utf-8",
    )
    _register_script(queue, str(script))
    task = queue.create(
        title,
        require_verification=True,
        evaluator_ref="review-loop",
        labels=["handoff"],
        target_worktree="wt-9",
    )
    queue.claim_one("m/wt-9", task_id=task.id, machine="m", worktree="wt-9")
    queue.start(task.id, "m/wt-9")
    queue.complete(task.id, "m/wt-9")

    released = []
    monkeypatch.setattr(
        handoff_claim_release,
        "release_if_handoff",
        lambda payload, task_id=None: released.append(
            (
                payload.get("id"),
                payload.get("target_worktree"),
                payload.get("status"),
            )
        ),
    )

    evaluate_submitted_task(queue, task.id, trigger="submitted")

    assert queue.get(task.id).status == expected_status
    assert released == [(task.id, "wt-9", expected_status)]


def test_verification_drain_retries_retryable_task_error(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = _submitted_task(
        queue,
        "retry me",
        require_verification=True,
        evaluator_ref="review-loop",
    )

    class _DrainBus:
        def publish(self, event: dict) -> None:
            pass

    calls = 0

    def fake_evaluate(queue_arg, task_id_arg, *, bus=None, trigger, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            with queue_arg._connect() as conn:
                conn.execute(
                    "UPDATE tasks SET updated_at = updated_at + 1 WHERE id = ?",
                    (task_id_arg,),
                )
            raise TaskError("task changed while the transition was in flight")
        queue_arg.confirm(task_id_arg, actor="evaluator")
        return {
            "task_id": task_id_arg,
            "trigger": trigger,
            "eligible": True,
            "reason": "submitted verification evaluated",
            "applied": [{"decision": "complete"}],
        }

    from agent_dispatch import verification_drain as verification_drain_module
    monkeypatch.setattr(verification_drain_module, "evaluate_submitted_task", fake_evaluate)

    async def scenario():
        loop = asyncio.create_task(
            drain_verification_requests(
                queue,
                _DrainBus(),
                interval=0.01,
                retry_base=0.01,
                max_attempts=3,
            )
        )
        try:
            for _ in range(200):
                status = queue.list_verification_requests(task_id)[0].status
                if (
                    status in {"stale", "delivered", "failed"}
                    and queue.get(task_id).status == Status.COMPLETED
                ):
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("verification request did not drain")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    [request] = queue.list_verification_requests(task_id)
    assert calls == 2
    assert request.status == "stale"
    assert request.attempts == 2
    assert queue.get(task_id).status == Status.COMPLETED


def test_verification_drain_cancel_does_not_wait_for_in_flight_thread_work(tmp_path):
    """Characterizes the race in aperture-labs#7895: ``Task.cancel()`` on a
    loop blocked inside ``asyncio.to_thread`` returns as soon as the
    cancellation propagates through asyncio -- it does NOT wait for the
    underlying OS thread to finish the real (synchronous) call. Under slow
    enough execution (e.g. coverage-instrumented test runs), a caller that
    proceeds with cleanup the instant ``await task`` returns can race that
    still-running thread.
    """
    queue = TaskQueue(tmp_path / "tasks.db")

    thread_finished = threading.Event()
    original_recover = queue.recover_inflight_verification_requests

    def slow_recover(*args, **kwargs):
        time.sleep(0.2)
        result = original_recover(*args, **kwargs)
        thread_finished.set()
        return result

    queue.recover_inflight_verification_requests = slow_recover

    async def scenario():
        task = asyncio.create_task(
            drain_verification_requests(queue, _Bus(), interval=0.01)
        )
        await asyncio.sleep(0.05)  # let the loop enter the slow to_thread call
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The defect this test characterizes: cancellation already returned,
        # but the background thread is still running the real DB call.
        assert not thread_finished.is_set()
        # Let the orphaned thread actually finish so it doesn't leak past the
        # test (and to prove it does eventually complete on its own).
        for _ in range(50):
            if thread_finished.is_set():
                break
            time.sleep(0.02)
        assert thread_finished.is_set()

    asyncio.run(scenario())


def test_verification_drain_stop_event_waits_for_in_flight_thread_work(tmp_path):
    """Regression test for aperture-labs#7895: a cooperative ``stop_event``
    must let the drain loop's current ``asyncio.to_thread`` call actually
    finish before the awaited task returns, unlike ``task.cancel()`` (see
    the companion characterization test above), so a caller can safely
    proceed with cleanup (e.g. a temp-dir teardown) once the await returns.
    """
    queue = TaskQueue(tmp_path / "tasks.db")

    thread_finished = threading.Event()
    original_recover = queue.recover_inflight_verification_requests

    def slow_recover(*args, **kwargs):
        time.sleep(0.2)
        result = original_recover(*args, **kwargs)
        thread_finished.set()
        return result

    queue.recover_inflight_verification_requests = slow_recover

    async def scenario():
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            drain_verification_requests(
                queue, _Bus(), interval=0.01, stop_event=stop_event
            )
        )
        await asyncio.sleep(0.05)  # let the loop enter the slow to_thread call
        stop_event.set()
        await task
        assert thread_finished.is_set(), (
            "await returned before the in-flight thread call finished"
        )

    asyncio.run(scenario())
