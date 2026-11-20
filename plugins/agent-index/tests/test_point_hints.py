"""Point-only intake, lossless versioned retention and old-claimant exclusion."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from agent_index import server
from agent_index.index_config import IndexConfig
from agent_index.indexing import engine, point_hints
from agent_index.indexing.change_hints import ChangeHints
from agent_index.indexing.change_hints_contract import execution_versions, intake_contract
from agent_index.indexing.point_hints import (
    CommentVersionTarget, PointBinding, PointChangeHints, WorkItemRevisionTarget,
    decode_change_hints, read_change_hints, union_change_hints,
)
from agent_index.indexing.task_store import TaskRecord, TaskStore
from agent_index.indexing.point_queue_consumer import PointQueueConsumer
from agent_index.indexing.worker import run_worker


def hints(**kwargs: Any) -> PointChangeHints:
    return PointChangeHints(**{
        "source": "ado:sample:points", "canonical_family": "ado:points",
        "account_id": "account-a",
        "binding": PointBinding(
            endpoint="https://example.test/collection/project",
            query_scope="query-union-a", query_context="definition-and-context-a",
        ),
        "targets": (WorkItemRevisionTarget("42", 7, ("event-a",)),),
        **kwargs,
    })


def test_point_dispatch_requires_explicit_config_before_opening_queue() -> None:
    calls: list[TaskRecord] = []
    assert run_worker("not-selected", point_executor=calls.append) == 2
    assert calls == []


@pytest.mark.parametrize("legacy", [False, True])
def test_point_dispatch_cannot_fabricate_completion_or_select_legacy_work(
    tmp_path: Path, legacy: bool,
) -> None:
    config = IndexConfig(data_dir=tmp_path)
    tasks = TaskStore(tmp_path / "tasks.db")
    task = tasks.enqueue("git:sample") if legacy else enqueue(tasks)
    calls: list[TaskRecord] = []

    def execute(selected: TaskRecord) -> TaskRecord:
        calls.append(selected)
        return replace(selected, status="complete", point_claim_id="not-a-receipt")

    assert run_worker(task.id, config=config, point_executor=execute) == 2
    assert tasks.get_task(task.id) == task
    assert calls == ([] if legacy else [task])


def enqueue(store: TaskStore, value: PointChangeHints | None = None) -> TaskRecord:
    value = value if value is not None else hints()
    return store.enqueue(value.source, change_hints=value)


def sql(path: Path, statement: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with sqlite3.connect(path) as conn:
        return conn.execute(statement, params).fetchall()


def record(store: TaskStore, task_id: str) -> TaskRecord:
    value = store.get_task(task_id)
    assert value is not None
    return value


def test_point_roundtrip_preserves_versions_parent_and_event_correlations() -> None:
    first = hints(targets=(
        WorkItemRevisionTarget("42", 7, ("event-a",)),
        CommentVersionTarget("42", "9", 2, ("event-b",)),
    ))
    second = hints(targets=(
        CommentVersionTarget("42", "9", 3, ("event-c",)),
        CommentVersionTarget("43", "9", 2, ("event-d",)),
        WorkItemRevisionTarget("42", 7, ("event-e",)),
    ))
    merged = first.union(second)
    assert set(merged.targets) == set(first.targets + second.targets)
    assert read_change_hints(merged.to_json()) == merged == decode_change_hints(merged.to_dict())
    assert merged == second.union(first) == merged.union(first)
    assert merged.to_json() == second.union(first).to_json()
    legacy = ChangeHints(source=first.source, canonical_family=first.canonical_family,
                         account_id=first.account_id, item_ids=("42:comment:9:3",))
    assert decode_change_hints(legacy.to_dict()) == legacy
    with pytest.raises(ValueError):
        ChangeHints.from_json(merged.to_json())
    with pytest.raises(ValueError, match="different"):
        union_change_hints(legacy, merged)


@pytest.mark.parametrize("target", [
    {"kind": "work_item_revision", "item_id": "42", "revision": True, "event_ids": []},
    {"kind": "work_item_revision", "item_id": "42", "revision": 0, "event_ids": []},
    {"kind": "comment_version", "item_id": "42", "comment_id": "9", "version": -1, "event_ids": []},
    {"kind": "comment_version", "item_id": "", "comment_id": "9", "version": 1, "event_ids": []},
    {"kind": "comment_version", "comment_id": "9", "version": 1, "event_ids": []},
    {"kind": "discussion", "item_id": "42"},
])
def test_invalid_or_incomplete_typed_target_refuses(target: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        decode_change_hints({**hints().to_dict(), "targets": [target]})


@pytest.mark.parametrize("change", [
    {"item_ids": ["42"]}, {"ref_ranges": []}, {"event_ids": ["unbound"]},
    {"targets": []}, {"version": True}, {"version": 3},
    {"binding": {"representation": "point-components"}},
])
def test_point_decoder_never_drops_unknown_fields_or_infers_binding(change: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        decode_change_hints({**hints().to_dict(), **change})


def test_duplicate_nested_fields_refuse() -> None:
    raw = hints().to_json().replace('"revision":7', '"revision":7,"revision":8')
    with pytest.raises(ValueError, match="Duplicate"):
        read_change_hints(raw)


@pytest.mark.parametrize("field,value", [
    ("source", "ado:other:points"), ("canonical_family", "ado:other"),
    ("account_id", "account-b"), ("endpoint", "https://other.test/project"),
    ("query_scope", "query-union-b"), ("query_context", "changed-definition"),
])
def test_queue_union_is_fenced_by_entire_binding(tmp_path: Path, field: str, value: str) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    first = hints()
    changes: dict[str, Any] = {field: value}
    if field in {"endpoint", "query_scope", "query_context"}:
        other = replace(first, binding=replace(first.binding, **changes))
    else:
        other = replace(first, **changes)
    a, b = enqueue(store, first), enqueue(store, other)
    assert a.id != b.id
    assert store.get_pending_count() == 2
    before = store.get_queue()
    assert store.next_point_task(point_scope=first.scope) == a
    assert store.next_point_task(point_scope=other.scope) == b
    assert store.get_queue() == before
    with pytest.raises(ValueError, match="different scopes"):
        first.union(other)


def test_point_selection_keeps_pinned_fifo_and_excludes_legacy_and_terminal_work(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    legacy = store.enqueue("git:sample")
    first = enqueue(store)
    pinned = store.pin_point_claim(first.id, expected_hints=hints())
    trailing = enqueue(store)
    assert trailing.id != pinned.id
    assert store.next_point_task(point_scope=hints().scope) == pinned
    assert store.cancel(pinned.id)
    assert store.next_point_task(point_scope=hints().scope) == trailing
    retried = store.retry(pinned.id)
    assert retried is not None and retried.point_claim_id == pinned.point_claim_id
    assert TaskStore(tmp_path / "tasks.db").next_point_task(point_scope=hints().scope) == retried
    assert store.cancel(retried.id) and store.cancel(trailing.id)
    assert store.next_point_task(point_scope=hints().scope) is None
    assert store.get_task(legacy.id) == legacy
    with pytest.raises(RuntimeError, match="exact typed point scope"):
        store.next_point_task(point_scope=cast(Any, ("all", *hints().scope[1:])))
    with pytest.raises(ValueError, match="exact point scope"):
        store.next_point_task(point_scope=cast(Any, None))
    with pytest.raises(ValueError, match="exact point scope"):
        PointQueueConsumer(
            config=IndexConfig(data_dir=tmp_path), point_scope=cast(Any, None),
            point_executor=lambda task: None,
        )


def test_point_consumer_refuses_fabricated_completion_and_requires_explicit_retry(
    tmp_path: Path,
) -> None:
    config = IndexConfig(data_dir=tmp_path)
    tasks = TaskStore(tmp_path / "tasks.db")
    task = enqueue(tasks)
    calls: list[TaskRecord] = []

    async def exercise() -> None:
        consumer = PointQueueConsumer(
            config=config, point_scope=hints().scope, point_executor=calls.append,
        )
        with pytest.raises(RuntimeError, match="not accepting"):
            await consumer.run_pending()
        async with consumer:
            with pytest.raises(RuntimeError, match="explicit resume"):
                await consumer.run_pending()
            await consumer.resume()
            for value in (0, -1, True):
                with pytest.raises(ValueError, match="task limit"):
                    await consumer.run_pending(max_tasks=value)
            for _ in range(2):
                with pytest.raises(RuntimeError, match="exited with code 2"):
                    await consumer.run_pending(max_tasks=2)
                assert consumer.paused and tasks.get_task(task.id) == task
                before = list(calls)
                with pytest.raises(RuntimeError, match="explicit resume"):
                    await consumer.run_pending()
                assert calls == before
                await consumer.resume()
        with pytest.raises(RuntimeError, match="not accepting"):
            await consumer.resume()
        with pytest.raises(RuntimeError, match="cannot be entered again"):
            async with consumer:
                pass

    asyncio.run(exercise())
    assert calls == [task, task] and tasks.get_task(task.id) == task


def test_point_consumer_rechecks_scope_at_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = IndexConfig(data_dir=tmp_path)
    tasks = TaskStore(tmp_path / "tasks.db")
    other_hints = hints(account_id="another-account")
    other = enqueue(tasks, other_hints)
    calls: list[TaskRecord] = []
    monkeypatch.setattr(TaskStore, "next_point_task", lambda *args, **kwargs: other)

    async def exercise() -> None:
        async with PointQueueConsumer(
            config=config, point_scope=hints().scope, point_executor=calls.append,
        ) as consumer:
            await consumer.resume()
            with pytest.raises(RuntimeError, match="exited with code 1"):
                await consumer.run_pending()
            assert consumer.paused and not calls
            assert tasks.get_task(other.id) == other

    asyncio.run(exercise())


def test_retention_reopen_retry_and_legacy_claimants_do_not_starve(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    first = enqueue(store)
    other = hints(targets=(CommentVersionTarget("42", "9", 2, ("event-b",)),))
    merged = enqueue(store, other)
    assert merged.change_hints is not None
    assert merged.id == first.id
    assert merged.status == "queued_hints_v2"
    assert merged.change_hints == hints().union(other)
    legacy = ChangeHints(source=hints().source, canonical_family=hints().canonical_family,
                         account_id=hints().account_id, item_ids=("42",))
    v1 = store.enqueue(legacy.source, change_hints=legacy)
    plain = store.enqueue("git:plain")
    full = store.enqueue("all", full=True)
    assert store.get_task(first.id) == merged
    # Exact selectors used by legacy and v1-only TaskStore generations.
    assert sql(path, "SELECT id FROM tasks WHERE status = 'queued' "
               "ORDER BY full DESC, created_at ASC LIMIT 1") == [(full.id,)]
    assert sql(path, "SELECT id FROM tasks WHERE status IN ('queued', 'queued_hints') "
               "ORDER BY full DESC, created_at ASC LIMIT 1") == [(full.id,)]
    assert record(store, plain.id).status == "cancelled"
    claimed = store.dequeue_next(accept_change_hints=True)
    assert claimed is not None and claimed.id == full.id
    claimed = store.dequeue_next(accept_change_hints=True)
    assert claimed is not None and claimed.id == v1.id
    assert store.dequeue_next(accept_change_hints=True) is None
    assert store.dequeue_next() is None
    assert store.get_all_tasks()["queued"] == [merged.to_dict()]
    store = TaskStore(path)
    assert store.cancel(first.id)
    retried = store.retry(first.id)
    assert retried is not None
    assert retried.status == "queued_hints_v2"
    assert retried.change_hints == merged.change_hints
    assert retried.attempt_count == 2
    assert store.get_pending_count() == 1
    assert record(store, v1.id).change_hints == legacy
    assert TaskStore(path).get_task(first.id) == retried
    # Old row readers decode only this column, including unfiltered history.
    # V2 stays opaque in its additive column rather than breaking unrelated UI.
    for (raw,) in sql(path, "SELECT change_hints FROM tasks"):
        if raw is not None:
            assert ChangeHints.from_json(raw) == legacy
    assert sql(path, "SELECT point_change_hints FROM tasks WHERE id = ?", (first.id,)) == [
        (merged.change_hints.to_json(),),
    ]


def test_persisted_fence_survives_old_status_retry_and_completion_sql(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    task = enqueue(store)
    for status in ("queued", "queued_hints"):
        sql(path, "UPDATE tasks SET status = ? WHERE id = ?", (status, task.id))
        assert record(store, task.id).status == "queued_hints_v2"
    before = sql(path, "SELECT * FROM tasks")
    for status in ("processing", "complete", "partial"):
        with pytest.raises(sqlite3.IntegrityError, match="v2 execution is unsupported"):
            sql(path, "UPDATE tasks SET status = ? WHERE id = ?", (status, task.id))
        assert sql(path, "SELECT * FROM tasks") == before
    for statement in (
        "UPDATE tasks SET change_hints_version = 1 WHERE id = ?",
        "UPDATE tasks SET point_change_hints = NULL WHERE id = ?",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            sql(path, statement, (task.id,))
    with pytest.raises(sqlite3.IntegrityError, match="worker not registered"):
        # A legacy direct worker registers itself before invoking the engine.
        sql(path, "UPDATE tasks SET worker_pid = 123, worker_host = 'test', "
            "worker_version = 'prior' WHERE id = ?", (task.id,))
    store.update_status(task.id, "interrupted")
    sql(path, "UPDATE tasks SET status = 'queued', error = NULL, started_at = NULL, "
        "finished_at = NULL, attempt_count = attempt_count + 1 "
        "WHERE id = ? AND status IN ('failed', 'cancelled', 'interrupted')", (task.id,))
    assert record(store, task.id).status == "queued_hints_v2"
    assert record(TaskStore(path), task.id).change_hints == task.change_hints


def test_pin_freezes_whole_claim_and_retains_trailing_union_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    original = hints(targets=(
        WorkItemRevisionTarget("42", 7, ("event-a",)),
        CommentVersionTarget("42", "9", 2, ("event-b",)),
        CommentVersionTarget("43", "9", 2, ("event-c",)),
    ))
    task = enqueue(store, original)
    pinned = store.pin_point_claim(task.id, expected_hints=original)
    assert pinned.point_claim_id is not None
    assert pinned.status == "queued_hints_v2"
    assert pinned.change_hints == original
    assert TaskStore(path).pin_point_claim(task.id, expected_hints=original) == pinned
    later = hints(targets=(
        WorkItemRevisionTarget("42", 8, ("event-d",)),
        CommentVersionTarget("42", "9", 3, ("event-e",)),
    ))
    trailing = enqueue(TaskStore(path), later)
    assert trailing.id != task.id and trailing.point_claim_id is None
    duplicate = enqueue(store, original)
    assert duplicate.id == trailing.id
    assert duplicate.change_hints == later.union(original)
    assert record(store, task.id) == pinned
    assert store.cancel(task.id)
    assert store.retry(task.id) is not None
    retried = record(TaskStore(path), task.id)
    assert retried.point_claim_id == pinned.point_claim_id
    assert retried.change_hints == original
    assert retried.attempt_count == 2
    assert enqueue(store, later).id == trailing.id
    assert store.dequeue_next(accept_change_hints=True) is None
    assert store.get_active_task() is None
    assert retried.to_dict()["point_claim_id"] == pinned.point_claim_id
    before = sql(path, "SELECT * FROM tasks ORDER BY id")
    for status in ("processing", "complete", "partial"):
        with pytest.raises(sqlite3.IntegrityError, match="v2 execution is unsupported"):
            store.update_status(task.id, status)
    with pytest.raises(sqlite3.IntegrityError, match="worker not registered"):
        store.set_worker(task.id, 123, "test", "future")
    assert sql(path, "SELECT * FROM tasks ORDER BY id") == before


def test_point_cancellation_cannot_reenter_an_active_source_writer(tmp_path: Path) -> None:
    from agent_index.indexing.source_ownership import source_writer

    store = TaskStore(tmp_path / "tasks.db")
    task = enqueue(store)
    pinned = store.pin_point_claim(task.id, expected_hints=hints())
    legacy = store.enqueue("git:plain")
    with source_writer(tmp_path):
        assert not store.cancel(task.id)
        assert record(store, task.id) == pinned
        assert store.cancel(legacy.id)
        with source_writer(tmp_path):
            assert record(store, task.id) == pinned
    assert store.cancel(task.id)
    retried = store.retry(task.id)
    assert retried is not None and retried.point_claim_id == pinned.point_claim_id
    assert retried.change_hints == pinned.change_hints


def test_pin_refuses_stale_or_partial_snapshot_without_losing_arrivals(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    original = hints()
    task = enqueue(store, original)
    later = hints(targets=(CommentVersionTarget("42", "9", 2, ("event-b",)),))
    merged = enqueue(store, later)
    before = sql(path, "SELECT * FROM tasks")
    for stale in (original, later, replace(original, account_id="another")):
        with pytest.raises(ValueError, match="no longer matches"):
            store.pin_point_claim(task.id, expected_hints=stale)
        assert sql(path, "SELECT * FROM tasks") == before
    pinned = store.pin_point_claim(task.id, expected_hints=original.union(later))
    assert pinned.change_hints == merged.change_hints
    with pytest.raises(ValueError, match="does not exist"):
        store.pin_point_claim("absent", expected_hints=original)
    assert store.cancel(task.id)
    before = sql(path, "SELECT * FROM tasks")
    with pytest.raises(ValueError, match="no longer matches"):
        store.pin_point_claim(task.id, expected_hints=original.union(later))
    assert sql(path, "SELECT * FROM tasks") == before


@pytest.mark.parametrize("assignment,value", [
    ("point_change_hints", hints(targets=(WorkItemRevisionTarget("42", 8),)).to_json()),
    ("point_change_hints", None), ("point_claim_id", None),
    ("point_claim_id", "a" * 32), ("source", "ado:other:points"),
    ("id", "replacement-task"), ("full", 1), ("change_hints_version", 1),
])
def test_pinned_claim_survives_legacy_union_and_reinitialization(
    tmp_path: Path, assignment: str, value: object,
) -> None:
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    task = enqueue(store)
    pinned = store.pin_point_claim(task.id, expected_hints=hints())
    # Prior installers recreate the shared hints trigger. The additive pin
    # trigger must protect the snapshot independently of that older definition.
    sql(path, "DROP TRIGGER tasks_hints_immutable")
    before = sql(path, "SELECT * FROM tasks")
    with pytest.raises(sqlite3.IntegrityError, match="Pinned point claim"):
        sql(path, f"UPDATE tasks SET {assignment} = ? WHERE id = ?", (value, task.id))
    assert sql(path, "SELECT * FROM tasks") == before
    assert record(TaskStore(path), task.id) == pinned


def test_point_pin_cannot_be_applied_to_plain_v1_or_terminal_work(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    plain = store.enqueue("git:plain")
    legacy = ChangeHints(source=hints().source, canonical_family=hints().canonical_family,
                         account_id=hints().account_id, item_ids=("42",))
    v1 = store.enqueue(legacy.source, change_hints=legacy)
    point = enqueue(store)
    assert store.cancel(point.id)
    before = sql(path, "SELECT * FROM tasks ORDER BY id")
    for task in (plain, v1, point):
        with pytest.raises(ValueError, match="no longer matches"):
            store.pin_point_claim(task.id, expected_hints=hints())
        with pytest.raises(sqlite3.IntegrityError, match="require a parked v2"):
            sql(path, "UPDATE tasks SET point_claim_id = ? WHERE id = ?", ("b" * 32, task.id))
    assert sql(path, "SELECT * FROM tasks ORDER BY id") == before
    assert "point_claim_id" not in record(store, plain.id).to_dict()


@pytest.mark.parametrize("subset", [False, True])
def test_legacy_duplicate_or_subset_union_cannot_absorb_post_pin_delivery(
    tmp_path: Path, subset: bool,
) -> None:
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    original = hints(targets=(
        WorkItemRevisionTarget("42", 7, ("event-a",)),
        CommentVersionTarget("42", "9", 2, ("event-b",)),
    ))
    task = enqueue(store, original)
    pinned = store.pin_point_claim(task.id, expected_hints=original)
    delivery = replace(original, targets=original.targets[:1]) if subset else original
    with sqlite3.connect(path) as conn:
        # Released v2 writers select any parked row, including a pinned one.
        conn.execute("BEGIN IMMEDIATE")
        selected = conn.execute(
            "SELECT id, point_change_hints FROM tasks WHERE status = 'queued_hints_v2' "
            "AND source = ? AND change_hints_version = 2 ORDER BY created_at, id",
            (original.source,),
        ).fetchone()
        assert selected is not None and selected[0] == task.id
        retained = read_change_hints(selected[1])
        assert isinstance(retained, PointChangeHints)
        unchanged = retained.union(delivery)
        assert unchanged == original
        with pytest.raises(sqlite3.IntegrityError, match="Pinned point claim"):
            conn.execute(
                "UPDATE tasks SET change_hints = NULL, point_change_hints = ?, "
                "updated_at = updated_at + 1 WHERE id = ?", (unchanged.to_json(), task.id),
            )
    assert record(store, task.id) == pinned
    trailing = enqueue(store, delivery)
    assert trailing.id != task.id and trailing.change_hints == delivery
    assert record(store, task.id) == pinned


def test_union_budget_refuses_atomically_and_duplicate_at_limit_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    monkeypatch.setattr(point_hints, "MAX_POINT_TARGETS", 2)
    first = hints(targets=(WorkItemRevisionTarget("42", 1), WorkItemRevisionTarget("42", 2)))
    enqueue(store, first)
    assert enqueue(store, first).change_hints == first
    before = sql(path, "SELECT * FROM tasks")
    with pytest.raises(ValueError, match="bounded"):
        enqueue(store, hints(targets=(WorkItemRevisionTarget("42", 3),)))
    assert sql(path, "SELECT * FROM tasks") == before
    monkeypatch.setattr(point_hints, "MAX_POINT_HINT_BYTES", len(first.to_json()) - 1)
    with pytest.raises(ValueError, match="too large"):
        hints(targets=first.targets)
    assert sql(path, "SELECT * FROM tasks") == before


def test_v2_worker_refuses_before_indexer_or_runtime_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = IndexConfig(data_dir=tmp_path)
    store = TaskStore(tmp_path / "tasks.db")
    task = enqueue(store)
    def forbidden(*args: Any, **kwargs: Any) -> dict[str, object]:
        pytest.fail("parked v2 claims must not execute")
    setattr(forbidden, "change_hints_versions", (1, 2))
    monkeypatch.setattr("agent_index.indexing.priority.lower_current_process_priority", forbidden)
    assert run_worker(task.id, config=config, indexer=forbidden) == 2
    assert store.get_task(task.id) == task
    assert execution_versions(forbidden) == [1]


def test_v2_http_intake_retains_whole_claim_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(server, "_missing_indexing_dependencies", lambda: [])
    monkeypatch.setattr(engine, "run_reindex", lambda **_: pytest.fail("intake must not execute"))
    store = TaskStore(tmp_path / "tasks.db")
    app = server.build_app()
    notifications: list[bool] = []
    app.state.task_store = store
    app.state.task_runner = SimpleNamespace(notify=lambda: notifications.append(True))
    client = TestClient(app)
    payload = {"source": hints().source, "change_hints": hints().to_dict()}
    response = client.post("/reindex/hints", json=payload)
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["accepted"] is True
    assert receipt["task"]["status"] == "queued_hints_v2"
    assert decode_change_hints(receipt["task"]["change_hints"]) == hints()
    assert receipt["indexing_contract"] == intake_contract()
    assert receipt["indexing_contract"]["intake_versions"] == [1, 2]
    assert receipt["indexing_contract"]["execution_versions"] == []
    assert notifications == [True]
    assert store.dequeue_next(accept_change_hints=True) is None
    for change in (
        {"source": "all"}, {"full": True},
        {"change_hints": {**hints().to_dict(), "item_ids": ["42"]}},
    ):
        assert client.post("/reindex/hints", json={**payload, **change}).status_code == 422
    assert store.get_pending_count() == 1
    assert notifications == [True]
    assert json.loads(sql(tmp_path / "tasks.db", "SELECT point_change_hints FROM tasks")[0][0]) == hints().to_dict()
    monkeypatch.setattr(point_hints, "MAX_POINT_TARGETS", 1)
    before = sql(tmp_path / "tasks.db", "SELECT * FROM tasks")
    other = hints(targets=(CommentVersionTarget("42", "9", 2),))
    assert client.post("/reindex/hints", json={
        "source": other.source, "change_hints": other.to_dict(),
    }).status_code == 409
    assert sql(tmp_path / "tasks.db", "SELECT * FROM tasks") == before
    assert notifications == [True]
