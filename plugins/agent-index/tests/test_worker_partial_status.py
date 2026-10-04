"""The worker must distinguish a clean run from one with per-source failures.

``run_reindex`` catches a per-source crawl/embed failure and continues past it
(#1350) so OTHER sources still index -- but that means the overall result can
have ``sources_failed`` non-empty while returning normally (no exception). The
worker used to always write ``TaskStatus.COMPLETE`` in that case, making a
single-source task whose one source failed indistinguishable from a genuinely
clean run without inspecting ``result_stats`` by hand. Bit twice in the same
corpus-fidelity session (#4948, #5004) before this status split landed.
"""

from __future__ import annotations

import asyncio

from agent_index.indexing import worker as worker_module
from agent_index.indexing.task_store import TaskStatus, TaskStore


def _store(tmp_path, monkeypatch):
    # Use the explicit, most-specific override (AGENT_INDEX_DATA_DIR) rather
    # than AGENT_INDEX_HOME: before the index_config.py fix landed (the fix
    # this leak prompted), IndexConfig.data_dir's default silently ignored
    # AGENT_INDEX_HOME entirely, so setting it here did NOT isolate this test
    # from the real production data directory -- every run of this test file
    # was writing real task rows into the operator's actual ~/.agent-index/data
    # (confirmed: 12 such rows, with THIS file's own exact mock fixtures,
    # ended up in production). AGENT_INDEX_DATA_DIR has always been honored
    # first, with no fallback ambiguity, so prefer it here even now that the
    # underlying default is fixed.
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path / "home" / "data"))
    from agent_index.index_config import IndexConfig

    cfg = IndexConfig()
    cfg.ensure_dirs()
    return TaskStore(cfg.data_dir / "tasks.db")


def _enqueue_and_run(tmp_path, monkeypatch, *, result: dict) -> TaskStatus:
    store = _store(tmp_path, monkeypatch)
    task = store.enqueue(source="git:repo", full=True)
    store.dequeue_next()  # claim it (queued -> processing), as the runner would

    from agent_index.indexing import engine as indexing_engine

    monkeypatch.setattr(indexing_engine, "run_reindex", lambda **_kwargs: result)

    rc = worker_module.run_worker(task.id)
    assert rc == 0
    return TaskStatus(store.get_task(task.id).status)


def test_clean_run_is_complete(tmp_path, monkeypatch) -> None:
    status = _enqueue_and_run(
        tmp_path,
        monkeypatch,
        result={"chunks_total": 4, "chunks_deleted": 0, "files_crawled": 2},
    )
    assert status == TaskStatus.COMPLETE


def test_run_with_a_failed_source_is_partial_not_complete(tmp_path, monkeypatch) -> None:
    status = _enqueue_and_run(
        tmp_path,
        monkeypatch,
        result={
            "chunks_total": 0,
            "chunks_deleted": 0,
            "files_crawled": 0,
            "sources_failed": [{"source": "git:repo", "error": "boom"}],
        },
    )
    assert status == TaskStatus.PARTIAL
    assert status != TaskStatus.COMPLETE


def test_partial_is_terminal_and_idempotent(tmp_path, monkeypatch) -> None:
    """A PARTIAL task must not be silently re-run (the idempotent no-op check
    worker.run_worker does for COMPLETE/CANCELLED must also cover PARTIAL)."""
    store = _store(tmp_path, monkeypatch)
    task = store.enqueue(source="git:repo", full=True)
    store.dequeue_next()
    store.update_status(task.id, TaskStatus.PARTIAL.value)

    calls: list[str] = []
    from agent_index.indexing import engine as indexing_engine

    monkeypatch.setattr(
        indexing_engine,
        "run_reindex",
        lambda **_kwargs: calls.append("ran") or {"chunks_total": 1},
    )

    rc = worker_module.run_worker(task.id)
    assert rc == 0
    assert calls == [], "PARTIAL is terminal -- must not re-run"


def test_partial_is_in_terminal_set() -> None:
    from agent_index.indexing.task_store import TERMINAL

    assert TaskStatus.PARTIAL in TERMINAL
    assert TaskStatus.COMPLETE in TERMINAL


def test_runner_counts_partial_as_failed_but_still_runs_post_index_hook() -> None:
    """``_monitor_worker`` must count PARTIAL toward ``tasks_failed`` (it is
    NOT a full success) while still invoking the post-index hook (PARTIAL
    content was genuinely stored for the sources that didn't fail, same as a
    clean COMPLETE needs post-processing)."""
    from agent_index.indexing import runner as runner_module

    class _FakeStore:
        def __init__(self, status: str) -> None:
            self._status = status

        def get_task(self, _task_id: str):
            return type(
                "Rec", (), {"status": self._status, "result_stats": None, "worker_pid": None}
            )()

    class _EventBus:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        def publish(self, event: str, payload: dict) -> None:
            self.events.append((event, payload))

    for status, expect_completed, expect_failed in (
        (TaskStatus.COMPLETE.value, 1, 0),
        (TaskStatus.PARTIAL.value, 0, 1),
    ):
        store = _FakeStore(status)
        bus = _EventBus()
        tr = runner_module.TaskRunner(store, bus)
        post_index_calls: list[str] = []
        tr.set_post_index_fn(lambda: post_index_calls.append("ran"))

        class _Task:
            id = "t1"

        asyncio.run(tr._monitor_worker(_Task(), None))

        assert tr.tasks_completed == expect_completed, status
        assert tr.tasks_failed == expect_failed, status
        assert post_index_calls == ["ran"], f"{status}: post-index hook must still run"
        success_flag = next(p["success"] for _e, p in bus.events if _e == "task_complete")
        assert success_flag == (status == TaskStatus.COMPLETE.value)
