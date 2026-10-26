"""Durable hinted queue semantics and legacy SQL rollback controls."""

from __future__ import annotations

import multiprocessing
import sqlite3

import pytest

from agent_index.indexing.change_hints import ChangeHints, MetadataRevision, RefRange
from agent_index.indexing.task_store import TaskRecord, TaskStore, _SCHEMA


def _hints(**kwargs):
    return ChangeHints(**{
        "source": "git:sample", "canonical_family": "git",
        "account_id": "account-a", "item_ids": ("a",), **kwargs,
    })


def _enqueue(store, hints=None):
    payload = hints if hints is not None else _hints()
    return store.enqueue(payload.source, change_hints=payload)


def _sql(path, statement, args=()):
    with sqlite3.connect(path) as conn:
        return conn.execute(statement, args).fetchall()


def _concurrent_enqueue(path, barrier, index):
    barrier.wait(timeout=15)
    store = TaskStore(path)
    _enqueue(store, _hints(item_ids=(f"item-{index}",), event_ids=(f"event-{index}",)))


def test_duplicates_union_all_identities_and_serialized_api(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    first_hints = _hints(
        ref_ranges=(RefRange("a", "b"),), event_ids=("event-1",),
        metadata_revisions=(MetadataRevision("a", "1"),),
    )
    first = _enqueue(store, first_hints)
    assert _enqueue(store, first_hints).id == first.id
    second_hints = _hints(
        item_ids=("b",), ref_ranges=(RefRange("a", "c"),),
        event_ids=("event-2",), metadata_revisions=(MetadataRevision("a", "2"),),
    )
    merged = _enqueue(store, second_hints)
    assert merged.id == first.id
    assert merged.change_hints == first_hints.union(second_hints)
    assert merged.to_dict()["change_hints"] == merged.change_hints.to_dict()
    assert merged.status == "queued_hints"
    assert store.get_pending_count() == 1
    assert store.get_queue() == [merged]
    assert store.get_history(status="queued_hints") == [merged]
    assert store.get_all_tasks()["queued"] == [merged.to_dict()]


def test_concurrent_processes_union_without_lost_updates(tmp_path):
    path = tmp_path / "tasks.db"
    # Leave a legacy DB for the concurrent constructors to migrate together.
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(4)
    processes = [context.Process(target=_concurrent_enqueue, args=(path, barrier, i)) for i in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=25)
        assert process.exitcode == 0
    store = TaskStore(path)
    assert store.get_pending_count() == 1
    task = store.get_queue()[0]
    assert task.change_hints.item_ids == tuple(f"item-{i}" for i in range(4))
    assert task.change_hints.event_ids == tuple(f"event-{i}" for i in range(4))


@pytest.mark.parametrize("field,value", [
    ("source", "git:alias"), ("canonical_family", "other"), ("account_id", "account-b"),
])
def test_source_family_and_account_fences(tmp_path, field, value):
    store = TaskStore(tmp_path / "tasks.db")
    first = _enqueue(store)
    second = _enqueue(store, _hints(**{field: value}))
    assert first.id != second.id
    assert store.get_pending_count() == 2


@pytest.mark.parametrize("full_source", ["all", "git:sample"])
@pytest.mark.parametrize("full_first", [False, True])
def test_legacy_full_never_covers_hints(tmp_path, full_source, full_first):
    store = TaskStore(tmp_path / "tasks.db")
    if full_first:
        full = store.enqueue(full_source, full=True)
        hinted = _enqueue(store)
    else:
        hinted = _enqueue(store)
        full = store.enqueue(full_source, full=True)
    assert hinted.id != full.id
    assert store.get_task(hinted.id).status == "queued_hints"
    assert store.dequeue_next(accept_change_hints=True).id == full.id
    assert store.dequeue_next(accept_change_hints=True).id == hinted.id


def test_active_hints_are_immutable_and_trailing_deliveries_union(tmp_path):
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    active = _enqueue(store)
    assert store.dequeue_next(accept_change_hints=True).id == active.id
    trailing = _enqueue(store, _hints(item_ids=("b",)))
    merged = _enqueue(store, _hints(item_ids=("c",)))
    assert active.id != trailing.id == merged.id
    assert store.get_task(active.id).change_hints == active.change_hints
    assert merged.change_hints.item_ids == ("b", "c")
    for statement, args in [
        ("UPDATE tasks SET change_hints = ? WHERE id = ?", (_hints(item_ids=("z",)).to_json(), active.id)),
        ("UPDATE tasks SET change_hints = NULL WHERE id = ?", (active.id,)),
        ("UPDATE tasks SET source = 'another' WHERE id = ?", (active.id,)),
        ("UPDATE tasks SET full = 1 WHERE id = ?", (active.id,)),
    ]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            _sql(path, statement, args)


def test_restart_retry_cancel_and_recovery_preserve_payload(tmp_path):
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    original = _enqueue(store, _hints(ref_ranges=(RefRange("a", "b"),)))
    assert store.dequeue_next() is None
    restarted = TaskStore(path)
    claimed = restarted.dequeue_next(accept_change_hints=True)
    assert claimed.change_hints == original.change_hints
    assert restarted.release_processing(original.id)
    assert restarted.get_task(original.id).status == "queued_hints"
    restarted.dequeue_next(accept_change_hints=True)
    assert restarted.mark_interrupted() == 1
    retried = restarted.retry(original.id)
    assert retried.change_hints == original.change_hints
    assert retried.attempt_count == 2
    assert retried.status == "queued_hints"
    assert restarted.cancel(original.id)
    assert restarted.retry(original.id).status == "queued_hints"
    restarted.dequeue_next(accept_change_hints=True)
    assert restarted.reap_orphaned("test-host", lambda _: False) == 1
    assert restarted.retry(original.id).change_hints == original.change_hints
    assert restarted.dequeue_next() is None


def test_legacy_dequeue_retry_and_release_sql_remain_fenced(tmp_path):
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    hinted = _enqueue(store)
    plain = store.enqueue("git:plain")
    # Released TaskStore's selector, retry and release SQL: it knows neither
    # the added column nor the parked status. The on-disk triggers survive it.
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
    selector = "SELECT id FROM tasks WHERE status = 'queued' ORDER BY full DESC, created_at ASC LIMIT 1"
    assert _sql(path, selector) == [(plain.id,)]
    store.dequeue_next()
    store.dequeue_next(accept_change_hints=True)
    _sql(path, "UPDATE tasks SET status = 'queued', started_at = NULL, worker_pid = NULL, "
         "worker_host = NULL, worker_version = NULL WHERE id = ? AND status = 'processing'", (hinted.id,))
    assert _sql(path, selector) == []
    store.update_status(hinted.id, "failed")
    _sql(path, "UPDATE tasks SET status = 'queued', error = NULL, started_at = NULL, "
         "finished_at = NULL, attempt_count = attempt_count + 1 "
         "WHERE id = ? AND status IN ('failed', 'cancelled', 'interrupted')", (hinted.id,))
    assert _sql(path, selector) == []
    assert store.get_task(hinted.id).status == "queued_hints"
    assert store.get_task(hinted.id).change_hints == hinted.change_hints
    store.update_status(hinted.id, "queued")
    assert _sql(path, selector) == []
    assert store.dequeue_next(accept_change_hints=True).id == hinted.id


def test_retry_with_existing_trailing_row_loses_neither_payload(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    first = _enqueue(store)
    store.dequeue_next(accept_change_hints=True)
    trailing = _enqueue(store, _hints(item_ids=("b",)))
    store.update_status(first.id, "failed")
    store.retry(first.id)
    _enqueue(store, _hints(item_ids=("c",)))
    queued = store.get_queue()
    assert {task.id for task in queued} == {first.id, trailing.id}
    assert set().union(*(task.change_hints.item_ids for task in queued)) == {"a", "b", "c"}


@pytest.mark.parametrize("payload", [
    "", "{", "null", "{}", _hints(source="git:other").to_json(),
])
def test_corruption_is_visible_before_claim_union_or_retry_mutates(tmp_path, payload):
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    task = _enqueue(store)
    _sql(path, "UPDATE tasks SET change_hints = ? WHERE id = ?", (payload, task.id))
    before = _sql(path, "SELECT * FROM tasks")
    for operation in [
        lambda: store.get_task(task.id), store.get_queue,
        lambda: store.dequeue_next(accept_change_hints=True),
        lambda: _enqueue(store),
    ]:
        with pytest.raises(ValueError):
            operation()
        assert _sql(path, "SELECT * FROM tasks") == before
    store.update_status(task.id, "failed")
    before_retry = _sql(path, "SELECT * FROM tasks")
    with pytest.raises(ValueError):
        store.retry(task.id)
    assert _sql(path, "SELECT * FROM tasks") == before_retry


def test_bad_enqueue_arguments_do_not_mutate_queue(tmp_path):
    path = tmp_path / "tasks.db"
    store = TaskStore(path)
    _enqueue(store)
    before = _sql(path, "SELECT * FROM tasks")
    for kwargs in [
        {"source": "git:other", "change_hints": _hints()},
        {"source": "git:sample", "full": True, "change_hints": _hints()},
        {"source": "git:sample", "change_hints": _hints().to_dict()},
    ]:
        with pytest.raises(ValueError):
            store.enqueue(**kwargs)
        assert _sql(path, "SELECT * FROM tasks") == before
    with pytest.raises(ValueError):
        store.dequeue_next(accept_change_hints="yes")
    assert _sql(path, "SELECT * FROM tasks") == before


def test_migration_keeps_legacy_rows_and_defaults(tmp_path):
    path = tmp_path / "tasks.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO tasks (id, source, created_at, updated_at) VALUES ('old', 'git:plain', 1, 1)"
        )
    store = TaskStore(path)
    old = store.get_task("old")
    assert old.change_hints is None
    assert old.status == "queued"
    assert "change_hints" not in old.to_dict()
    assert store.dequeue_next().id == old.id
    assert store.enqueue("git:new").change_hints is None
    _enqueue(store)
    assert TaskStore(path).get_pending_count() == 2


def test_task_record_preserves_positional_constructor(tmp_path):
    task = TaskStore(tmp_path / "tasks.db").enqueue("git:plain")
    values = [getattr(task, name) for name in task.__dataclass_fields__ if name != "change_hints"]
    assert TaskRecord(*values) == task
