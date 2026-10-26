"""Actual worker exits preserve parked hints through monitor and capability recovery."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs

from agent_index.indexing import engine, runner as runner_module
from agent_index.indexing.change_hints import ChangeHints
from agent_index.indexing.runner import TaskRunner
from agent_index.indexing.task_store import TaskStore


def _hints():
    return ChangeHints(
        source="git:sample", canonical_family="git", account_id="account-a", item_ids=("a",),
    )


def test_actual_worker_rollback_monitor_then_capability_restoration(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path / "data"))

    def capable(**kwargs):
        raise AssertionError("the parent schedules; only the child executes")

    capable.change_hints_versions = (1,)
    monkeypatch.setattr(engine, "run_reindex", capable)
    source = Path(__file__).resolve().parents[1] / "src"
    child_code = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agent_index.indexing import engine, priority, worker
deadline = time.monotonic() + 8
while not Path(sys.argv[3]).exists() and time.monotonic() < deadline:
    time.sleep(0.01)
assert Path(sys.argv[3]).exists()
def run_reindex(**kwargs):
    assert kwargs["change_hints"].item_ids == ("a",)
    return {"chunks_total": 1}
if sys.argv[4] == "capable":
    run_reindex.change_hints_versions = (1,)
engine.run_reindex = run_reindex
priority.lower_current_process_priority = lambda *_: None
raise SystemExit(worker.run_worker(sys.argv[2]))
"""

    async def exercise():
        store = TaskStore(tmp_path / "data" / "tasks.db")
        task = store.enqueue("git:sample", change_hints=_hints())
        events = []
        runner = TaskRunner(store, SimpleNamespace(publish=lambda *event: events.append(event)))
        runner.set_governance_recheck_fn(lambda _: {"status": "ready"})
        lock = asyncio.Lock()
        children = []
        starts = []

        async def acquire(_):
            await lock.acquire()
            return lock

        def spawn(task_id):
            start = tmp_path / f"start-{len(children)}"
            mode = "legacy" if not children else "capable"
            child = subprocess.Popen(
                [sys.executable, "-c", child_code, str(source), task_id, str(start), mode],
                env=os.environ.copy(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                **no_window_kwargs(),
            )
            children.append(child)
            starts.append(start)
            return child

        runner.set_source_lock_fn(acquire)
        monkeypatch.setattr(runner, "_spawn_worker", spawn)
        try:
            for expected_code in (2, 0):
                await runner._worker_iteration()
                monitor = runner._monitors[task.id]
                child = children[-1]
                assert lock.locked()
                starts[-1].write_text("start", encoding="utf-8")
                _, stderr = await asyncio.to_thread(child.communicate, timeout=12)
                assert child.returncode == expected_code, stderr.decode()
                await asyncio.wait_for(monitor, timeout=5)
                assert not lock.locked()
                assert not runner._active_workers
                assert runner.tasks_failed == 0
                record = store.get_task(task.id)
                assert record.change_hints == task.change_hints
                assert record.attempt_count == 1
                if expected_code == 2:
                    assert record.status == "queued_hints"
                    assert runner.tasks_completed == 0
                    assert store.dequeue_next() is None
                    assert not any(kind == "task_complete" for kind, _ in events)
                else:
                    assert record.status == "complete"
                    assert runner.tasks_completed == 1
        finally:
            for start in starts:
                start.write_text("start", encoding="utf-8")
            for child in children:
                await asyncio.to_thread(child.communicate, timeout=12)
            for monitor in list(runner._monitors.values()):
                await asyncio.wait_for(monitor, timeout=5)

    asyncio.run(exercise())


@pytest.mark.parametrize("cancelled", [False, True])
def test_monitor_rereads_parked_record_after_exit_and_adopted_pid_is_retained(tmp_path, monkeypatch, cancelled):
    store = TaskStore(tmp_path / "tasks.db")
    task = store.enqueue("git:sample", change_hints=_hints())
    processing = store.dequeue_next(accept_change_hints=True)
    processing = replace(processing, worker_pid=12345)
    assert store.release_processing(task.id)
    events = []
    runner = TaskRunner(store, SimpleNamespace(publish=lambda *event: events.append(event)))
    if cancelled:
        runner._cancel_requested.add(task.id)
    actual_get = store.get_task
    reads = []

    def get_task(task_id):
        reads.append(task_id)
        return processing if len(reads) == 1 else actual_get(task_id)

    monkeypatch.setattr(store, "get_task", get_task)
    observed_pids = []
    monkeypatch.setattr(
        runner_module, "_pid_alive", lambda pid: observed_pids.append(pid) or False,
    )
    asyncio.run(runner._monitor_worker(processing, None))
    assert observed_pids == [12345]
    assert actual_get(task.id).status == ("cancelled" if cancelled else "queued_hints")
    assert runner.tasks_failed == 0
    if not cancelled:
        assert ("queue_update", {"task_id": task.id, "reason": "capability-rollback"}) in events
