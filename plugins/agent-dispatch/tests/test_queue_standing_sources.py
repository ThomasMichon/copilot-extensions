"""Complete same-source reconciliation keeps native history and ordering fences."""

from dataclasses import asdict, replace
import json
import subprocess
import sys

import pytest

from agent_dispatch.queue import TaskError, TaskQueue
from tests import test_standing_charters as charters

REPO = "owner/repo"
declaration, enroll, queue = charters.declaration, charters.enroll, charters.queue


def entry(declaration, **changes):
    return {"title": "Dispatcher", "declaration": asdict(declaration), **changes}


def reconcile(queue, entries, revision="commit-1", expected=None):
    return queue.reconcile_standing_source_set(
        REPO, "owner", entries, revision=revision, expected_observation=expected,
    )


def event_count(queue):
    with queue._connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]


def test_repeat_restart_change_and_last_removal(queue, declaration):
    first = reconcile(queue, [entry(declaration)])
    task_id = first["enrolled"][0]
    restarted = TaskQueue(queue.db_path)
    repeated = reconcile(restarted, [entry(declaration)], expected="commit-1")
    assert repeated["unchanged"] == [task_id]
    assert event_count(restarted) == 1
    assert restarted.standing_source_observation(REPO, "owner") == "commit-1"

    updated = replace(declaration, source_revision="source-2")
    changed = reconcile(restarted, [entry(updated, title="Renamed")], "commit-2", "commit-1")
    assert changed["changed"] == [task_id]
    current = restarted.get(task_id)
    assert current.title == "Renamed" and current.standing_revision == 2
    assert current.standing_charter["declaration"] == asdict(updated)

    removed = reconcile(restarted, [], "commit-3", "commit-2")
    assert removed["retired"] == [{"id": task_id, "reason": "removed"}]
    assert restarted.get(task_id).standing_charter["lifecycle"] == "retiring"
    assert restarted.get(task_id).status == "queued"
    assert event_count(restarted) == 3
    reconcile(restarted, [], "commit-3", "commit-3")
    assert event_count(restarted) == 3


def test_first_observation_never_retires_absent_preexisting_lane(queue, declaration):
    task = enroll(queue, declaration)
    first = reconcile(queue, [])
    assert first["deferred"] == [{"id": task.id, "reason": "first-source-observation"}]
    assert queue.get(task.id) == task
    next_read = reconcile(queue, [], "commit-2", "commit-1")
    assert next_read["retired"] == [{"id": task.id, "reason": "removed"}]


def test_unordered_source_can_enroll_but_not_change_disable_or_remove(queue, declaration):
    first = reconcile(queue, [entry(declaration)], None)
    task_id = first["enrolled"][0]
    task = queue.get(task_id)
    assert first["observation"] is None
    assert reconcile(queue, [entry(declaration)], None)["unchanged"] == [task_id]
    for entries, reason in (
        ([entry(declaration, title="Changed")], "unordered-source-change"),
        ([entry(declaration, enabled=False)], "unordered-source-disabled"),
        ([], "first-source-observation"),
    ):
        assert reconcile(queue, entries, None)["deferred"] == [{"id": task_id, "reason": reason}]
        assert queue.get(task_id) == task
    reconcile(queue, [entry(declaration)])
    assert reconcile(queue, [], None, "commit-1")["deferred"] == [
        {"id": task_id, "reason": "unordered-source-removed"},
    ]
    assert queue.standing_source_observation(REPO, "owner") == "commit-1"
    assert event_count(queue) == 1


def test_disable_retains_holds_and_never_resurrects(queue, declaration):
    first = reconcile(queue, [entry(declaration)])
    task_id = first["enrolled"][0]
    queue.set_hold(task_id, reason="operator hold", actor="operator")
    disabled = reconcile(queue, [entry(declaration, enabled=False)], "commit-2", "commit-1")
    assert disabled["retired"] == [{"id": task_id, "reason": "disabled"}]
    task = queue.get(task_id)
    assert task.hold_reason == "operator hold"
    with pytest.raises(TaskError, match="held"):
        queue.settle_standing_retirement(task_id, expected_revision=task.standing_revision)
    again = reconcile(queue, [entry(declaration)], "commit-3", "commit-2")
    assert again["deferred"] == [{"id": task_id, "reason": "reenrollment-required"}]
    assert queue.get(task_id) == task


def test_stale_observation_has_no_partial_effects(queue, declaration):
    first = reconcile(queue, [entry(declaration)])
    task_id = first["enrolled"][0]
    before = queue.get(task_id)
    for expected in (None, "older"):
        with pytest.raises(TaskError, match="observation changed"):
            reconcile(queue, [entry(replace(declaration, lane_key="new"))], "newer", expected)
    assert queue.get(task_id) == before
    assert event_count(queue) == 1
    assert queue.standing_source_observation(REPO, "owner") == "commit-1"


@pytest.mark.parametrize("invalid", ["duplicate", "foreign", "fields", "entry", "enabled"])
def test_invalid_set_rejected_atomically(queue, declaration, invalid):
    good = entry(declaration)
    bad = {
        "duplicate": good,
        "foreign": entry(replace(declaration, namespace="another")),
        "fields": {**good, "declaration": {"namespace": "owner"}},
        "entry": None,
        "enabled": {**good, "enabled": 0},
    }[invalid]
    with pytest.raises(TaskError):
        reconcile(queue, [good, bad])
    assert event_count(queue) == 0
    assert queue.standing_source_observation(REPO, "owner") is None


def test_source_scope_does_not_retire_foreign_repo_or_namespace(queue, declaration):
    own = enroll(queue, declaration)
    other_repo = queue.reconcile_standing_charter("Other", declaration, repo="owner/other")
    other_namespace = enroll(queue, replace(declaration, namespace="another"))
    reconcile(queue, [entry(declaration)])
    reconcile(queue, [], "commit-2", "commit-1")
    assert queue.get(own.id).standing_charter["lifecycle"] == "retiring"
    assert queue.get(other_repo.id) == other_repo
    assert queue.get(other_namespace.id) == other_namespace


def test_source_inventory_includes_empty_fences_and_unobserved_charters_only(queue, declaration):
    queue.create("Finite", repo="owner/finite")
    queue.reconcile_standing_charter(
        "Foreign", replace(declaration, namespace="another"), repo="owner/foreign",
    )
    assert queue.standing_source_repositories("owner") == []
    own = enroll(queue, declaration)
    queue.reconcile_standing_source_set(
        "owner/empty", "owner", [], revision="commit-1", expected_observation=None,
    )
    assert queue.standing_source_repositories("owner") == ["owner/empty", REPO]
    reconcile(queue, [entry(declaration)])
    reconcile(queue, [], "commit-2", "commit-1")
    current = queue.get(own.id)
    queue.settle_standing_retirement(own.id, expected_revision=current.standing_revision)
    restarted = TaskQueue(queue.db_path)
    assert restarted.standing_source_repositories("owner") == ["owner/empty", REPO]
    assert restarted.standing_source_repositories("another") == ["owner/foreign"]
    assert restarted.standing_source_repositories("unknown") == []


@pytest.mark.parametrize("namespace", ["", " owner", "owner\n", None])
def test_source_inventory_rejects_invalid_namespace(queue, namespace):
    with pytest.raises(TaskError, match="namespace"):
        queue.standing_source_repositories(namespace)


def test_competing_processes_cannot_apply_the_same_predecessor(queue, declaration):
    reconcile(queue, [entry(declaration)])
    script = """
import json, sys
from agent_dispatch.queue import TaskQueue, TaskError
queue = TaskQueue(sys.argv[1])
try:
    queue.reconcile_standing_source_set(
        'owner/repo', 'owner', [json.loads(sys.argv[2])],
        revision=sys.argv[3], expected_observation='commit-1')
except TaskError as exc:
    assert 'observation changed' in str(exc)
    print('stale')
else:
    print('applied')
"""
    children = []
    try:
        for index in range(2):
            candidate = entry(declaration, title=f"Candidate {index}")
            children.append(subprocess.Popen(
                [sys.executable, "-c", script, str(queue.db_path), json.dumps(candidate), f"r{index}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ))
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, stderr
            results.append(stdout.strip())
        assert sorted(results) == ["applied", "stale"]
        assert event_count(queue) == 2
        assert queue.standing_source_observation(REPO, "owner") in {"r0", "r1"}
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)
