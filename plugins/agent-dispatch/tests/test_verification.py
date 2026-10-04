from __future__ import annotations

import asyncio
import json
import sys
import threading
import time

import pytest

from agent_dispatch import handoff_claim_release
from agent_dispatch import remote_dispatch
from agent_dispatch.github_provider_adapter import PRObservation
from agent_dispatch.pr_observation_store import PRObservationStore
from agent_dispatch.queue import Status, TaskError
from agent_dispatch.verification import evaluate_submitted_task
from agent_dispatch.verification_drain import (
    _scheduled_wait_interval,
    drain_verification_requests,
)
from agent_dispatch.provider_state_machine import ApprovalStatus, Mergeability, Revision
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
    repo: str = TEST_REPO,
    payload_inline: str | None = None,
    payload_ref: str | None = None,
) -> str:
    task = queue.create(
        title,
        repo=repo,
        require_verification=require_verification,
        evaluator_ref=evaluator_ref,
        payload_inline=payload_inline,
        payload_ref=payload_ref,
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
    evaluator_ref: str = "review-loop",
    reviewer_loop: dict | None = None,
) -> None:
    spec = {
        "repo": repo,
        "evaluator_ref": evaluator_ref,
        "evaluator_spec": {
            "scripts": {evaluator_ref: [sys.executable, script_path]}
        },
    }
    if reviewer_loop is not None:
        spec["reviewer_loop"] = reviewer_loop
    queue.register_registration(
        "evaluator",
        spec,
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


def test_reviewer_loop_stale_after_days_is_per_declaration(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "tasks.db")
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "json.dump({'decision': 'noop', 'reason': 'still-open'}, sys.stdout)\n",
        encoding="utf-8",
    )
    _register_script(
        queue,
        str(script),
        repo="example/repo-seven",
        evaluator_ref="review-loop-seven",
        reviewer_loop={"stale_after_days": 7},
    )
    _register_script(
        queue,
        str(script),
        repo="example/repo-thirty",
        evaluator_ref="review-loop-thirty",
        reviewer_loop={"stale_after_days": 30},
    )
    last_commit_at = 1_000_000.0
    payload = json.dumps(
        {
            "reviewer_loop": {
                "last_commit_at": last_commit_at,
                "observed_at": last_commit_at + (31 * 86400.0),
            }
        }
    )
    seven_day_id = _submitted_task(
        queue,
        "review repo-seven",
        repo="example/repo-seven",
        require_verification=True,
        evaluator_ref="review-loop-seven",
        payload_inline=payload,
    )
    thirty_day_id = _submitted_task(
        queue,
        "review repo-thirty",
        repo="example/repo-thirty",
        require_verification=True,
        evaluator_ref="review-loop-thirty",
        payload_inline=payload,
    )

    monkeypatch.setattr(
        "agent_dispatch.reviewer_loops.time.time",
        lambda: last_commit_at + (8 * 86400.0),
    )
    seven_day = evaluate_submitted_task(queue, seven_day_id, trigger="submitted")
    thirty_day = evaluate_submitted_task(queue, thirty_day_id, trigger="submitted")

    assert seven_day["applied"][0]["decision"] == "abandon"
    assert queue.get(seven_day_id).status == Status.ABANDONED
    assert thirty_day["applied"][0]["decision"] == "noop"
    assert queue.get(thirty_day_id).status == Status.SUBMITTED

    monkeypatch.setattr(
        "agent_dispatch.reviewer_loops.time.time",
        lambda: last_commit_at + (31 * 86400.0),
    )
    thirty_day_late = evaluate_submitted_task(queue, thirty_day_id, trigger="backfill")

    assert thirty_day_late["applied"][0]["decision"] == "abandon"
    assert queue.get(thirty_day_id).status == Status.ABANDONED


def test_reviewer_loop_without_stale_after_days_never_stales(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "tasks.db")
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "json.dump({'decision': 'noop', 'reason': 'still-open'}, sys.stdout)\n",
        encoding="utf-8",
    )
    _register_script(
        queue,
        str(script),
        repo="example/repo-none",
        evaluator_ref="review-loop-none",
    )
    last_commit_at = 1_000_000.0
    task_id = _submitted_task(
        queue,
        "review repo-none",
        repo="example/repo-none",
        require_verification=True,
        evaluator_ref="review-loop-none",
        payload_inline=json.dumps({"reviewer_loop": {"last_commit_at": last_commit_at}}),
    )
    monkeypatch.setattr(
        "agent_dispatch.reviewer_loops.time.time",
        lambda: last_commit_at + (365 * 86400.0),
    )

    report = evaluate_submitted_task(queue, task_id, trigger="submitted")

    assert report["applied"][0]["decision"] == "noop"
    assert queue.get(task_id).status == Status.SUBMITTED


def test_reviewer_loop_stale_deadline_requeues_and_fires_without_manual_evaluate(
    tmp_path, monkeypatch
):
    queue = TaskQueue(tmp_path / "tasks.db")
    current_time = {"value": 1_000_000.0}
    monkeypatch.setattr(
        queue, "_now", lambda now=None: current_time["value"] if now is None else float(now)
    )
    monkeypatch.setattr(
        "agent_dispatch.reviewer_loops.time.time", lambda: current_time["value"]
    )
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "json.dump({'decision': 'noop', 'reason': 'still-open'}, sys.stdout)\n",
        encoding="utf-8",
    )
    _register_script(
        queue,
        str(script),
        repo="example/repo-delayed",
        evaluator_ref="review-loop-delayed",
        reviewer_loop={"stale_after_days": 7},
    )
    store = PRObservationStore(tmp_path / "pr-observations.db")
    task_id = _submitted_task(
        queue,
        "review repo-delayed",
        repo="example/repo-delayed",
        require_verification=True,
        evaluator_ref="review-loop-delayed",
        payload_ref="github-pr:example/repo-delayed#1",
    )
    store.put(
        "example/repo-delayed",
        1,
        PRObservation(
            number=1,
            approval_status=ApprovalStatus.APPROVED,
            mergeability=Mergeability.CLEAN,
            holds=frozenset(),
            revision=Revision(diff_hash="head-1", base_sha="base-1"),
            last_commit_at=current_time["value"],
        ),
        observed_at=current_time["value"],
    )
    signal: asyncio.Queue[None] = asyncio.Queue()

    class _DrainBus:
        def publish(self, event: dict) -> None:
            return None

    async def scenario():
        loop = asyncio.create_task(
            drain_verification_requests(
                queue,
                _DrainBus(),
                interval=0.01,
                idle_interval=0.1,
                retry_base=0.01,
                max_attempts=3,
                signal=signal,
            )
        )
        try:
            for _ in range(200):
                requests = queue.list_verification_requests(task_id)
                delayed = next(
                    (
                        request
                        for request in requests
                        if request.trigger == "reviewer-loop-stale-deadline"
                        and request.status == "pending"
                    ),
                    None,
                )
                if delayed is not None:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("future verification request was not scheduled")

            delayed = next(
                request
                for request in queue.list_verification_requests(task_id)
                if request.trigger == "reviewer-loop-stale-deadline"
            )
            assert delayed.trigger == "reviewer-loop-stale-deadline"
            assert delayed.not_before == current_time["value"] + (7 * 86400.0)
            assert queue.get(task_id).status == Status.SUBMITTED

            current_time["value"] += 8 * 86400.0
            store.put(
                "example/repo-delayed",
                1,
                PRObservation(
                    number=1,
                    approval_status=ApprovalStatus.APPROVED,
                    mergeability=Mergeability.CLEAN,
                    holds=frozenset(),
                    revision=Revision(diff_hash="head-1", base_sha="base-1"),
                    last_commit_at=1_000_000.0,
                ),
                observed_at=current_time["value"],
            )
            signal.put_nowait(None)
            for _ in range(200):
                if queue.get(task_id).status == Status.ABANDONED:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("stale deadline did not auto-abandon the task")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert queue.get(task_id).status == Status.ABANDONED


def test_reviewer_loop_can_derive_last_commit_at_from_provider_observation_store(
    tmp_path, monkeypatch
):
    install_root = tmp_path / "install-root"
    install_root.mkdir()
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(install_root))
    last_commit_at = 1_000_000.0
    queue = TaskQueue(install_root / "tasks.db")
    store = PRObservationStore(install_root / "pr-observations.db")
    store.put(
        "example/provider-repo",
        7,
        PRObservation(
            number=7,
            approval_status=ApprovalStatus.APPROVED,
            mergeability=Mergeability.CLEAN,
            holds=frozenset(),
            revision=Revision(diff_hash="head-1", base_sha="base-1"),
            last_commit_at=last_commit_at,
        ),
        observed_at=last_commit_at + (8 * 86400.0),
    )
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "json.dump({'decision': 'noop', 'reason': 'still-open'}, sys.stdout)\n",
        encoding="utf-8",
    )
    _register_script(
        queue,
        str(script),
        repo="example/provider-repo",
        evaluator_ref="review-loop-provider",
        reviewer_loop={"stale_after_days": 7},
    )
    monkeypatch.setattr(
        "agent_dispatch.reviewer_loops.time.time",
        lambda: last_commit_at + (8 * 86400.0),
    )
    task_id = _submitted_task(
        queue,
        "review via provider store",
        repo="example/provider-repo",
        require_verification=True,
        evaluator_ref="review-loop-provider",
        payload_ref="github-pr:example/provider-repo#7",
    )

    report = evaluate_submitted_task(queue, task_id, trigger="submitted")

    assert report["applied"][0]["decision"] == "abandon"
    assert queue.get(task_id).status == Status.ABANDONED


def test_reviewer_loop_stale_after_days_works_with_blob_spilled_payload(
    tmp_path, monkeypatch
):
    queue = TaskQueue(tmp_path / "tasks.db", blob_threshold=16)
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "json.dump({'decision': 'noop', 'reason': 'still-open'}, sys.stdout)\n",
        encoding="utf-8",
    )
    _register_script(
        queue,
        str(script),
        repo="example/repo-blob",
        evaluator_ref="review-loop-blob",
        reviewer_loop={"stale_after_days": 7},
    )
    last_commit_at = 1_000_000.0
    monkeypatch.setattr(
        "agent_dispatch.reviewer_loops.time.time",
        lambda: last_commit_at + (8 * 86400.0),
    )
    task_id = _submitted_task(
        queue,
        "review repo-blob",
        repo="example/repo-blob",
        require_verification=True,
        evaluator_ref="review-loop-blob",
        payload_inline=json.dumps(
            {
                "reviewer_loop": {
                    "last_commit_at": last_commit_at,
                    "observed_at": last_commit_at + (8 * 86400.0),
                },
                "padding": "x" * 200,
            }
        ),
    )
    task = queue.get(task_id)
    assert task is not None and task.payload_inline is None and task.payload_ref is not None

    report = evaluate_submitted_task(queue, task_id, trigger="submitted")

    assert report["applied"][0]["decision"] == "abandon"
    assert queue.get(task_id).status == Status.ABANDONED


def test_reviewer_loop_stale_after_days_applies_even_when_evaluator_raises(
    tmp_path, monkeypatch
):
    queue = TaskQueue(tmp_path / "tasks.db")
    script = tmp_path / "eval.py"
    script.write_text(
        "import sys\n"
        "raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    _register_script(
        queue,
        str(script),
        repo="example/repo-raise",
        evaluator_ref="review-loop-raise",
        reviewer_loop={"stale_after_days": 7},
    )
    last_commit_at = 1_000_000.0
    monkeypatch.setattr(
        "agent_dispatch.reviewer_loops.time.time",
        lambda: last_commit_at + (8 * 86400.0),
    )
    task_id = _submitted_task(
        queue,
        "review repo-raise",
        repo="example/repo-raise",
        require_verification=True,
        evaluator_ref="review-loop-raise",
        payload_inline=json.dumps(
            {
                "reviewer_loop": {
                    "last_commit_at": last_commit_at,
                    "observed_at": last_commit_at + (8 * 86400.0),
                }
            }
        ),
    )

    report = evaluate_submitted_task(queue, task_id, trigger="submitted")

    assert report["applied"][0]["decision"] == "abandon"
    assert queue.get(task_id).status == Status.ABANDONED


def test_reviewer_loop_stale_after_days_requires_fresh_provider_observation(
    tmp_path, monkeypatch
):
    install_root = tmp_path / "install-root"
    install_root.mkdir()
    observation_db = install_root / "custom" / "tasks.db"
    observation_db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENT_DISPATCH_DB", str(observation_db))
    last_commit_at = 1_000_000.0
    deadline = last_commit_at + (7 * 86400.0)
    store = PRObservationStore(observation_db.parent / "pr-observations.db")
    store.put(
        "example/provider-repo",
        9,
        PRObservation(
            number=9,
            approval_status=ApprovalStatus.APPROVED,
            mergeability=Mergeability.CLEAN,
            holds=frozenset(),
            revision=Revision(diff_hash="head-1", base_sha="base-1"),
            last_commit_at=last_commit_at,
        ),
        observed_at=deadline - 10.0,
    )
    queue = TaskQueue(observation_db)
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "json.dump({'decision': 'noop', 'reason': 'still-open'}, sys.stdout)\n",
        encoding="utf-8",
    )
    _register_script(
        queue,
        str(script),
        repo="example/provider-repo",
        evaluator_ref="review-loop-provider-freshness",
        reviewer_loop={"stale_after_days": 7},
    )
    monkeypatch.setattr(
        "agent_dispatch.reviewer_loops.time.time",
        lambda: deadline + 60.0,
    )
    task_id = _submitted_task(
        queue,
        "review provider freshness",
        repo="example/provider-repo",
        require_verification=True,
        evaluator_ref="review-loop-provider-freshness",
        payload_ref="github-pr:example/provider-repo#9",
    )

    report = evaluate_submitted_task(queue, task_id, trigger="submitted")

    assert report["applied"][0]["decision"] == "noop"
    assert queue.get(task_id).status == Status.SUBMITTED


def test_reviewer_loop_stale_after_days_uses_relocated_observation_store_path(
    tmp_path, monkeypatch
):
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    queue_db = relocated / "nested" / "tasks.db"
    queue_db.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENT_DISPATCH_DB", str(queue_db))
    last_commit_at = 1_000_000.0
    store = PRObservationStore(queue_db.parent / "pr-observations.db")
    store.put(
        "example/provider-repo",
        11,
        PRObservation(
            number=11,
            approval_status=ApprovalStatus.APPROVED,
            mergeability=Mergeability.CLEAN,
            holds=frozenset(),
            revision=Revision(diff_hash="head-1", base_sha="base-1"),
            last_commit_at=last_commit_at,
        ),
        observed_at=last_commit_at + (8 * 86400.0),
    )
    queue = TaskQueue(queue_db)
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "json.dump({'decision': 'noop', 'reason': 'still-open'}, sys.stdout)\n",
        encoding="utf-8",
    )
    _register_script(
        queue,
        str(script),
        repo="example/provider-repo",
        evaluator_ref="review-loop-provider-relocated",
        reviewer_loop={"stale_after_days": 7},
    )
    monkeypatch.setattr(
        "agent_dispatch.reviewer_loops.time.time",
        lambda: last_commit_at + (8 * 86400.0),
    )
    task_id = _submitted_task(
        queue,
        "review relocated provider store",
        repo="example/provider-repo",
        require_verification=True,
        evaluator_ref="review-loop-provider-relocated",
        payload_ref="github-pr:example/provider-repo#11",
    )

    report = evaluate_submitted_task(queue, task_id, trigger="submitted")

    assert report["applied"][0]["decision"] == "abandon"
    assert queue.get(task_id).status == Status.ABANDONED


def test_backlog_triager_verification_checks_repo_specific_label_schema_and_effort_marker(
    tmp_path,
):
    queue = TaskQueue(tmp_path / "tasks.db")
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, re, sys\n"
        "fixtures = {\n"
        "  17: {\n"
        "    'labels': ['bug', 'triage:accepted', 'priority:high'],\n"
        "    'body': 'Active effort: `efforts/active/example/README.md`',\n"
        "  },\n"
        "  18: {\n"
        "    'labels': ['bug', 'triage:accepted', 'priority:high'],\n"
        "    'body': 'triaged, but no effort linked yet',\n"
        "  },\n"
        "  19: {\n"
        "    'labels': ['triage:duplicate'],\n"
        "    'body': 'duplicate of #17; no effort link needed',\n"
        "  },\n"
        "}\n"
        "task = json.load(sys.stdin)['task']\n"
        "payload = json.loads(task['payload_inline'])\n"
        "keys = payload['repository_issue_loop']['resource_keys']\n"
        "numbers = [int(re.search(r':issue:(\\d+)$', key).group(1)) for key in keys]\n"
        "for number in numbers:\n"
        "    issue = fixtures[number]\n"
        "    labels = set(issue['labels'])\n"
        "    active_bug = 'triage:accepted' in labels and 'bug' in labels\n"
        "    if not active_bug:\n"
        "        continue\n"
        "    has_priority = any(label.startswith('priority:') for label in labels)\n"
        "    has_effort = 'efforts/active/' in issue['body'] and '/README.md' in issue['body']\n"
        "    if not (has_priority and has_effort):\n"
        "        json.dump({'decision': 'noop', 'reason': 'triage schema incomplete'}, sys.stdout)\n"
        "        break\n"
        "else:\n"
        "    json.dump({'decision': 'confirm', 'reason': 'triage schema + effort marker present'}, sys.stdout)\n",
        encoding="utf-8",
    )
    _register_script(queue, str(script), evaluator_ref="backlog-triager")
    good_id = _submitted_task(
        queue,
        "triaged backlog issue",
        require_verification=True,
        evaluator_ref="backlog-triager",
        payload_inline='{"repository_issue_loop":{"resource_keys":["forge:github:repository:example/project:issue:17"]}}',
    )
    incomplete_id = _submitted_task(
        queue,
        "still missing effort marker",
        require_verification=True,
        evaluator_ref="backlog-triager",
        payload_inline='{"repository_issue_loop":{"resource_keys":["forge:github:repository:example/project:issue:18"]}}',
    )
    duplicate_id = _submitted_task(
        queue,
        "duplicate report closed without effort link",
        require_verification=True,
        evaluator_ref="backlog-triager",
        payload_inline='{"repository_issue_loop":{"resource_keys":["forge:github:repository:example/project:issue:19"]}}',
    )

    good = evaluate_submitted_task(queue, good_id, trigger="submitted")
    incomplete = evaluate_submitted_task(queue, incomplete_id, trigger="submitted")
    duplicate = evaluate_submitted_task(queue, duplicate_id, trigger="submitted")

    assert good["applied"][0]["decision"] == "complete"
    assert queue.get(good_id).status == Status.COMPLETED
    assert incomplete["applied"][0]["decision"] == "noop"
    assert queue.get(incomplete_id).status == Status.SUBMITTED
    assert duplicate["applied"][0]["decision"] == "complete"
    assert queue.get(duplicate_id).status == Status.COMPLETED


def test_future_scheduled_verification_uses_idle_interval_not_retry_interval(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create(
        "retry later",
        require_verification=True,
        evaluator_ref="review-loop",
    )
    with queue._connect() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (Status.SUBMITTED, 1_000.0, task.id),
        )
    queue.schedule_submitted_verification(
        task.id,
        trigger="reviewer-loop-stale-deadline",
        not_before=10_000.0,
        now=1_000.0,
    )

    assert queue.next_pending_verification_not_before() == 10_000.0
    assert _scheduled_wait_interval(
        now=1_000.0,
        next_not_before=10_000.0,
        retry_interval=0.25,
        idle_interval=5.0,
    ) == 5.0


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
    """Characterizes a real async-cancellation race: ``Task.cancel()`` on a
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
    """Regression test: a cooperative ``stop_event``
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
