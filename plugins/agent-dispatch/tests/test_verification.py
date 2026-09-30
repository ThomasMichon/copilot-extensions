from __future__ import annotations

import sys

from agent_dispatch.queue import Status
from agent_dispatch.verification import evaluate_submitted_task
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue


class _Bus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, event: dict) -> None:
        self.events.append(event)


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


def _register_script(queue: TaskQueue, script_path: str, *, repo: str = TEST_REPO, env: str = "default") -> None:
    queue.register_registration(
        "evaluator",
        {
            "repo": repo,
            "evaluator_ref": "review-loop",
            "evaluator_spec": {
                "scripts": {"review-loop": [sys.executable, script_path]}
            },
        },
        machine="lambda-core",
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
        queue, task_id, trigger="submitted", current_machine="lambda-core"
    )
    skipped = evaluate_submitted_task(
        queue, untouched.id, trigger="backfill", current_machine="lambda-core"
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
