"""Configured native owners and queue pins through the actual acquisition caller."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

import test_azure_devops_point_worker as worker

from agent_index.engine.client import EngineClient
from agent_index.indexing.point_hints import WorkItemRevisionTarget
from agent_index.indexing.task_store import TaskRecord, TaskStatus
from agent_index.indexing.worker import run_worker
from agent_index.sources.azure_devops_point_runtime import run_configured_azure_devops_points
from agent_index.store.source_layout import SourceLayoutError

subject = worker.subject
application = worker.application


def run(f: worker.Fixture, task: TaskRecord, **kwargs: Any) -> TaskRecord:
    options: dict[str, Any] = {
        "expected_hints": f.claim.hints, "config": f.config,
        "legacy_data_dir": f.config.data_dir.parent / "legacy",
        "point_scope": f.claim.hints.scope, "scope": worker.SCOPE,
        "clients": {model: cast(EngineClient, worker.Client(f, model))
                    for model in f.config.model_profiles},
        "max_rows": 32,
    }
    return run_configured_azure_devops_points(f.connector, task.id, **(options | kwargs))


def test_configured_runtime_pins_executes_and_retries_without_ordinary_dequeue(
    application: worker.Fixture,
) -> None:
    f = application
    original = f.tasks.get_task(f.claim.task_id)
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    assert task.point_claim_id is None and f.tasks.dequeue_next(accept_change_hints=True) is None
    poll = f.config.state_file.read_bytes()
    result = run(f, task)
    assert result.status == TaskStatus.COMPLETE and result.point_claim_id is not None
    assert result.worker_pid is None and f.provider.body_reads == 2 and f.calls
    assert f.tasks.get_task(f.claim.task_id) == original
    f.reopen()
    f.provider.user = "changed"
    assert run(f, result, clients={}) == result
    assert not f.provider.requests and not f.calls
    assert f.config.state_file.read_bytes() == poll


@pytest.mark.parametrize(
    "selection", ["hints", "domain", "models", "model_subset", "connector", "clients"],
)
def test_configured_mismatch_preserves_unpinned_snapshot(
    application: worker.Fixture, selection: str,
) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    options: dict[str, Any] = {}
    if selection in ("hints", "domain"):
        changed = replace(f.claim.hints, account_id="other")
        options["point_scope"] = changed.scope
        if selection == "domain":
            options["expected_hints"] = changed
    elif selection in ("models", "model_subset"):
        profiles = dict(f.config.model_profiles)
        if selection == "models":
            profiles["first"] = replace(profiles["first"], dim=3)
        else:
            profiles.pop("first")
        options["config"] = replace(f.config, model_profiles=profiles)
    elif selection == "connector":
        f.connector._work_item_queries = []
    else:
        options["clients"] = {}
    with pytest.raises((ValueError, SourceLayoutError)):
        run(f, task, **options)
    assert f.tasks.get_task(task.id) == task
    assert not f.provider.requests and not f.calls


def test_changed_queue_snapshot_is_not_pinned_or_acquired(application: worker.Fixture) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    current = f.tasks.enqueue(f.claim.hints.source, change_hints=replace(
        f.claim.hints, targets=(WorkItemRevisionTarget("7", 9),),
    ))
    assert current.id == task.id
    with pytest.raises(ValueError, match="parked target set"):
        run(f, task)
    assert f.tasks.get_task(task.id) == current
    assert not f.provider.requests and not f.calls


def test_fresh_composition_reuses_retained_acquisition_after_interruption(
    application: worker.Fixture,
) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    def interrupt() -> None:
        raise RuntimeError("fixture interrupted embedding")
    f.hook = interrupt
    assert run_worker(
        task.id, config=f.config, point_executor=lambda selected: run(f, selected),
    ) == 1
    pinned = f.tasks.get_task(task.id)
    assert pinned is not None and pinned.point_claim_id is not None
    assert pinned.status == TaskStatus.QUEUED_HINTS_V2 and f.retained_count() == 2
    f.reopen()
    f.hook = None
    assert run_worker(
        task.id, config=f.config, point_executor=lambda selected: run(f, selected),
    ) == 0
    completed = f.tasks.get_task(task.id)
    assert completed is not None and completed.status == TaskStatus.COMPLETE
    assert f.provider.body_reads == 0 and f.calls


def test_selected_point_dispatch_owns_synchronous_lifetime_without_process_setup(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_index.indexing import priority, worker as dispatcher

    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Point dispatch must not configure or register a detached process")

    monkeypatch.setattr(priority, "lower_current_process_priority", forbidden)
    monkeypatch.setattr(dispatcher, "_install_signal_handlers", forbidden)
    monkeypatch.setattr(type(f.tasks), "set_worker", forbidden)
    assert run_worker(task.id, config=f.config) == 2
    assert not f.provider.requests and f.tasks.get_task(task.id) == task

    def execute(selected: TaskRecord) -> TaskRecord:
        assert selected == task
        result = run(f, selected)
        assert result.status == TaskStatus.COMPLETE
        return result

    assert run_worker(task.id, config=f.config, point_executor=execute) == 0
    completed = f.tasks.get_task(task.id)
    assert completed is not None and completed.status == TaskStatus.COMPLETE
    assert completed.worker_pid is None and f.provider.body_reads == 2 and f.calls
    f.reopen()
    f.provider.user = "changed"
    assert run_worker(
        task.id, config=f.config, point_executor=lambda selected: run(f, selected, clients={}),
    ) == 0
    assert not f.provider.requests and not f.calls


def test_executor_mutation_cannot_redirect_completion_to_another_task(
    application: worker.Fixture,
) -> None:
    f = application
    selected = f.tasks.get_task(f.claim.task_id)
    assert selected is not None
    completed = run(f, selected)
    assert completed.status == TaskStatus.COMPLETE
    original = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    assert original.id != completed.id and original.point_claim_id is None

    def redirect(task: TaskRecord) -> None:
        task.__dict__.update(completed.__dict__)

    assert run_worker(original.id, config=f.config, point_executor=redirect) == 2
    assert f.tasks.get_task(original.id) == original
    assert f.tasks.get_task(completed.id) == completed


@pytest.mark.parametrize("overlap", ["same", "ancestor", "descendant", "symlink"])
def test_legacy_overlap_refuses_before_pin_or_provider(
    application: worker.Fixture, overlap: str, tmp_path: Path,
) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    legacy = {
        "same": f.config.data_dir, "ancestor": f.config.data_dir.parent,
        "descendant": f.config.data_dir / "legacy", "symlink": tmp_path / "alias",
    }[overlap]
    if overlap == "symlink":
        legacy.symlink_to(f.config.data_dir, target_is_directory=True)
    with pytest.raises(ValueError, match="separate legacy"):
        run(f, task, legacy_data_dir=legacy)
    assert f.tasks.get_task(task.id) == task and not f.provider.requests


def test_missing_domain_is_not_initialized(application: worker.Fixture, tmp_path: Path) -> None:
    f = application
    config = replace(f.config, data_dir=tmp_path / "unprepared")
    task = f.tasks.get_task(f.claim.task_id)
    assert task is not None
    with pytest.raises(ValueError, match="existing configured"):
        run(f, task, config=config)
    assert not config.data_dir.exists() and not f.provider.requests


def test_aliased_child_state_cannot_escape_the_selected_directory(
    application: worker.Fixture, tmp_path: Path,
) -> None:
    f = application
    task = f.tasks.enqueue(f.claim.hints.source, change_hints=f.claim.hints)
    foreign = tmp_path / "foreign-clusters.db"
    foreign.write_bytes(b"untouched")
    f.config.clusters_db.symlink_to(foreign)
    with pytest.raises(ValueError, match="inside its selected"):
        run(f, task)
    assert f.tasks.get_task(task.id) == task and foreign.read_bytes() == b"untouched"
    assert not f.provider.requests
