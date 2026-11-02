"""Capable worker/runner/CLI routing, with explicit legacy and rollback controls."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from agent_index import __main__ as cli
from agent_index.indexing import engine, priority, worker
from agent_index.indexing.change_hints import ChangeHints, RefRange
from agent_index.indexing.runner import IndexingCancelled, TaskRunner
from agent_index.indexing.task_store import TaskStore


def _hints(**kwargs):
    return ChangeHints(**{
        "source": "git:sample", "canonical_family": "git",
        "account_id": "account-a", "item_ids": ("a",),
        "ref_ranges": (RefRange("base", "target"),), **kwargs,
    })


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(priority, "lower_current_process_priority", lambda *_: None)
    monkeypatch.setattr(worker, "_install_signal_handlers", lambda *_: None)
    monkeypatch.setattr("agent_index.config.machine_id", lambda: "test-host")
    monkeypatch.setattr("agent_index.transport.maybe_delegate", lambda *_: None)
    return TaskStore(tmp_path / "data" / "tasks.db")


def _enqueue(store, hints=None):
    return store.enqueue("git:sample", change_hints=hints or _hints())


def _capable(monkeypatch, fn):
    fn.change_hints_versions = (1,)
    monkeypatch.setattr(engine, "run_reindex", fn)
    return fn


def test_worker_preserves_plain_legacy_call_signature(store, monkeypatch):
    calls = []

    def legacy(*, full, source, progress_cb, resume_since):
        calls.append((full, source, resume_since))
        return {"chunks_total": 1}

    monkeypatch.setattr(engine, "run_reindex", legacy)
    task = store.enqueue("git:sample")
    store.dequeue_next()
    assert worker.run_worker(task.id) == 0
    assert calls == [(False, "git:sample", None)]
    assert store.get_task(task.id).status == "complete"


def test_unclaimed_hints_cannot_be_run_directly(store, monkeypatch):
    _capable(monkeypatch, lambda **kwargs: pytest.fail("must be claimed"))
    task = _enqueue(store)
    assert worker.run_worker(task.id) == 2
    assert store.get_task(task.id).status == "queued_hints"


def test_worker_rolls_back_to_parked_for_legacy_selected_callable(store, monkeypatch):
    monkeypatch.setattr(engine, "run_reindex", lambda **kwargs: pytest.fail("must not execute"))
    monkeypatch.setattr(engine, "CHANGE_HINTS_VERSIONS", (1,), raising=False)
    task = _enqueue(store)
    store.dequeue_next(accept_change_hints=True)
    assert worker.run_worker(task.id) == 2
    parked = store.get_task(task.id)
    assert parked.status == "queued_hints"
    assert parked.change_hints == task.change_hints
    assert parked.worker_pid is None
    assert store.dequeue_next() is None


def test_capable_worker_receives_exact_hints_and_trailing_burst_stays_separate(store, monkeypatch):
    task = _enqueue(store)
    store.dequeue_next(accept_change_hints=True)
    captured = []

    def capable(**kwargs):
        captured.append(kwargs)
        assert store.get_task(task.id).change_hints == task.change_hints
        first = _enqueue(store, _hints(item_ids=("b",)))
        second = _enqueue(store, _hints(item_ids=("c",)))
        assert first.id == second.id != task.id
        return {"chunks_total": 1}

    _capable(monkeypatch, capable)
    assert worker.run_worker(task.id) == 0
    assert captured[0]["change_hints"] == task.change_hints
    assert captured[0]["full"] is False
    assert captured[0]["source"] == "git:sample"
    assert store.get_task(task.id).status == "complete"
    assert store.get_queue()[0].change_hints.item_ids == ("b", "c")


def test_failure_retry_retains_hints_and_resume_window(store, monkeypatch):
    captured = []

    def fails(**kwargs):
        captured.append(kwargs)
        raise RuntimeError("acquisition interrupted")

    _capable(monkeypatch, fails)
    task = _enqueue(store)
    first = store.dequeue_next(accept_change_hints=True)
    assert worker.run_worker(task.id) == 1
    assert store.get_task(task.id).status == "failed"
    assert store.retry(task.id).change_hints == task.change_hints
    store.dequeue_next(accept_change_hints=True)
    _capable(monkeypatch, lambda **kwargs: captured.append(kwargs) or {"chunks_total": 1})
    assert worker.run_worker(task.id) == 0
    assert captured[0]["change_hints"] == captured[1]["change_hints"] == task.change_hints
    assert captured[1]["resume_since"] == first.first_started_at


def test_worker_cancellation_preserves_payload_and_is_idempotent(store, monkeypatch):
    calls = []

    def cancels(**kwargs):
        calls.append(kwargs["change_hints"])
        raise IndexingCancelled("cancel requested")

    _capable(monkeypatch, cancels)
    task = _enqueue(store)
    store.dequeue_next(accept_change_hints=True)
    assert worker.run_worker(task.id) == 0
    assert store.get_task(task.id).status == "cancelled"
    assert worker.run_worker(task.id) == 0
    assert calls == [task.change_hints]
    assert store.retry(task.id).status == "queued_hints"


def test_cancellation_before_execution_never_calls_engine(store, monkeypatch):
    _capable(monkeypatch, lambda **kwargs: pytest.fail("cancelled before acquisition"))
    monkeypatch.setattr(worker, "_install_signal_handlers", lambda cancelled: cancelled.set())
    task = _enqueue(store)
    store.dequeue_next(accept_change_hints=True)
    assert worker.run_worker(task.id) == 0
    assert store.get_task(task.id).status == "cancelled"
    assert store.get_task(task.id).change_hints == task.change_hints


def _runner(store, monkeypatch):
    runner = TaskRunner(store, SimpleNamespace(publish=lambda *_: None))
    runner.set_governance_recheck_fn(lambda _: {"status": "ready"})
    launched = []

    async def launch(task):
        launched.append(task)

    monkeypatch.setattr(runner, "_launch_worker", launch)
    return runner, launched


def test_runner_does_not_infer_support_from_registered_wrapper_or_module(store, monkeypatch):
    monkeypatch.setattr(engine, "run_reindex", lambda **kwargs: None)
    monkeypatch.setattr(engine, "CHANGE_HINTS_VERSIONS", (1,), raising=False)
    hinted = _enqueue(store)
    plain = store.enqueue("git:plain")
    runner, launched = _runner(store, monkeypatch)
    runner.set_index_fn(_capable_wrapper())
    asyncio.run(runner._worker_iteration())
    assert [task.id for task in launched] == [plain.id]
    assert store.get_task(hinted.id).status == "queued_hints"


def _capable_wrapper():
    def wrapper(**kwargs):
        pytest.fail("registered wrapper is not the selected worker callable")

    wrapper.change_hints_versions = (1,)
    return wrapper


def test_runner_capable_dequeue_and_prelaunch_rollback(store, monkeypatch):
    _capable(monkeypatch, lambda **kwargs: None)
    task = _enqueue(store)
    runner, launched = _runner(store, monkeypatch)
    asyncio.run(runner._worker_iteration())
    assert [record.id for record in launched] == [task.id]
    assert launched[0].change_hints == task.change_hints
    store.release_processing(task.id)
    launched.clear()

    def recheck(checkpoint):
        if checkpoint == "pre-mutation:launch":
            monkeypatch.setattr(engine, "run_reindex", lambda **kwargs: None)
        return {"status": "ready"}

    runner.set_governance_recheck_fn(recheck)
    asyncio.run(runner._worker_iteration())
    assert launched == []
    assert store.get_task(task.id).status == "queued_hints"
    assert store.dequeue_next() is None


def test_runner_claim_then_worker_rollback_does_not_execute(store, monkeypatch):
    calls = []
    _capable(monkeypatch, lambda **kwargs: calls.append(kwargs) or {})
    task = _enqueue(store)
    runner, launched = _runner(store, monkeypatch)
    asyncio.run(runner._worker_iteration())
    assert launched[0].id == task.id
    monkeypatch.setattr(engine, "run_reindex", lambda **kwargs: pytest.fail("rolled back"))
    assert worker.run_worker(task.id) == 2
    assert calls == []
    assert store.get_task(task.id).status == "queued_hints"


def test_cli_hinted_and_legacy_calls_preserve_semantics(store, monkeypatch, tmp_path, capsys):
    path = tmp_path / "hints.json"
    path.write_text(_hints().to_json(), encoding="utf-8")
    calls = []
    _capable(monkeypatch, lambda **kwargs: calls.append(kwargs) or {"chunks_total": 1})
    assert cli.main(["index", "--source", "git:sample", "--change-hints-file", str(path)]) == 0
    assert calls[0] == {"full": False, "source": "git:sample", "change_hints": _hints()}
    assert json.loads(capsys.readouterr().out)["chunks_total"] == 1
    assert cli.main(["index", "--source", "git:sample", "--full"]) == 0
    assert calls[1] == {"full": True, "source": "git:sample"}


@pytest.mark.parametrize("args", [
    [], ["--source", "git:other"], ["--source", "git:sample", "--full"],
])
def test_cli_bad_scope_refuses_before_engine_call(store, monkeypatch, tmp_path, args):
    path = tmp_path / "hints.json"
    path.write_text(_hints().to_json(), encoding="utf-8")
    _capable(monkeypatch, lambda **kwargs: pytest.fail("invalid scope"))
    assert cli.main(["index", *args, "--change-hints-file", str(path)]) == 1


def test_cli_selected_legacy_callable_refuses_hints_without_fallback(store, monkeypatch, tmp_path):
    path = tmp_path / "hints.json"
    path.write_text(_hints().to_json(), encoding="utf-8")
    monkeypatch.setattr(engine, "run_reindex", lambda **kwargs: pytest.fail("unsupported"))
    monkeypatch.setattr(engine, "CHANGE_HINTS_VERSIONS", (1,), raising=False)
    assert cli.main(["index", "--source", "git:sample", "--change-hints-file", str(path)]) == 1


@pytest.mark.parametrize("payload", [
    "{", '{"version":1,"version":2}', json.dumps({**_hints().to_dict(), "version": 2}),
])
def test_cli_invalid_hint_payload_never_calls_engine(store, monkeypatch, tmp_path, payload):
    path = tmp_path / "hints.json"
    path.write_text(payload, encoding="utf-8")
    _capable(monkeypatch, lambda **kwargs: pytest.fail("invalid hints"))
    assert cli.main(["index", "--source", "git:sample", "--change-hints-file", str(path)]) == 1


def test_cli_unknown_hint_flag_is_rejected(store, monkeypatch):
    monkeypatch.setattr(engine, "run_reindex", lambda **kwargs: pytest.fail("bad flag"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["index", "--change-hint-file", "unused.json"])
    assert exc.value.code == 2
