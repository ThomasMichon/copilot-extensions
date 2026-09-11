"""Phase 10 (``review-automation-reliability`` effort, live-wiring pass)
regression tests: prove ``TaskQueue``'s public lifecycle methods actually
*source* their legal-transition data from
``agent_dispatch.task_state_machine.TRANSITIONS_BY_NAME`` rather than a
separately hardcoded, coincidentally-matching set of literals.

Each test monkeypatches one declared transition's ``from_states``/
``to_state`` and asserts the corresponding live method's behavior changes
to match -- this is what proves genuine wiring rather than parity that
happens to hold today and could silently drift tomorrow. Complements
``test_task_state_machine.py`` (which only proves the declared table is
internally sound) and ``test_queue.py`` (which proves the live behavior is
correct against today's real hardcoded literals).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_dispatch import task_state_machine
from agent_dispatch.queue import Status, TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def _patch_transition(monkeypatch, name: str, **overrides):
    """Replace one declared transition in
    ``task_state_machine.TRANSITIONS_BY_NAME`` with a modified copy, live
    for the duration of the test. ``queue.py`` reads this table lazily
    (once per call), so the patch takes effect on the very next call."""
    original = task_state_machine.TRANSITIONS_BY_NAME[name]
    patched = replace(original, **overrides)
    new_table = dict(task_state_machine.TRANSITIONS_BY_NAME)
    new_table[name] = patched
    monkeypatch.setattr(task_state_machine, "TRANSITIONS_BY_NAME", new_table)


def test_approve_is_sourced_from_the_declared_table_not_a_literal(q, monkeypatch):
    """Narrowing ``approve``'s declared ``from_states`` to an empty set must
    make the live call refuse a proposed task it would otherwise accept --
    proof the live code reads the table, not a hardcoded copy of it."""
    task = q.create("t", status=Status.PROPOSED)
    _patch_transition(monkeypatch, "approve", from_states=frozenset())
    with pytest.raises(TaskError):
        q.approve(task.id)


def test_approve_honors_a_widened_declared_to_state(q, monkeypatch):
    """Redirecting ``approve``'s declared ``to_state`` must change where the
    live call actually lands the task."""
    task = q.create("t", status=Status.PROPOSED)
    _patch_transition(monkeypatch, "approve", to_state=Status.ABANDONED)
    result = q.approve(task.id)
    assert result.status == Status.ABANDONED


def test_start_is_sourced_from_the_declared_table(q, monkeypatch):
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    _patch_transition(monkeypatch, "start", from_states=frozenset())
    with pytest.raises(TaskError):
        q.start(claimed.id, "worker-1")


def test_suspend_is_sourced_from_the_declared_table(q, monkeypatch):
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    q.start(claimed.id, "worker-1")
    _patch_transition(monkeypatch, "suspend", from_states=frozenset())
    with pytest.raises(TaskError):
        q.suspend(claimed.id, "worker-1", reason="pausing")


def test_yield_task_is_sourced_from_the_declared_table(q, monkeypatch):
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    _patch_transition(monkeypatch, "yield_task", from_states=frozenset())
    with pytest.raises(TaskError):
        q.yield_task(claimed.id, "worker-1")


def test_resume_is_sourced_from_the_declared_table(q, monkeypatch):
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    q.start(claimed.id, "worker-1")
    q.suspend(claimed.id, "worker-1", reason="pausing")
    _patch_transition(monkeypatch, "resume", from_states=frozenset())
    with pytest.raises(TaskError):
        q.resume(claimed.id, "worker-1")


def test_release_suspended_is_sourced_from_the_declared_table(q, monkeypatch):
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    q.start(claimed.id, "worker-1")
    q.suspend(claimed.id, "worker-1", reason="pausing")
    _patch_transition(monkeypatch, "release_suspended", from_states=frozenset())
    with pytest.raises(TaskError):
        q.release_suspended(claimed.id, "worker-1")


def test_abandon_is_sourced_from_the_declared_table(q, monkeypatch):
    task = q.create("t", status=Status.PROPOSED)
    _patch_transition(monkeypatch, "abandon", from_states=frozenset())
    with pytest.raises(TaskError):
        q.abandon(task.id, permitted=True)


def test_complete_from_started_is_sourced_from_the_declared_table(q, monkeypatch):
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    q.start(claimed.id, "worker-1")
    _patch_transition(monkeypatch, "complete", from_states=frozenset())
    with pytest.raises(TaskError):
        q.complete(claimed.id, "worker-1")


def test_complete_from_suspended_still_allowed_after_phase_10_correction(q):
    """The ``complete`` transition's declared ``from_states`` was corrected
    during Phase 10's live-wiring pass to include ``suspended`` (the real
    ``complete_with_outcome`` always allowed this; the original Phase 9
    declaration only named ``started``). This proves the corrected
    declaration and the live behavior now genuinely agree, not just by
    accident."""
    assert task_state_machine.TRANSITIONS_BY_NAME["complete"].from_states == frozenset(
        {Status.STARTED, Status.SUSPENDED}
    )
    task = q.create("t", status=Status.PROPOSED)
    q.approve(task.id)
    claimed = q.claim_one("worker-1")
    assert claimed is not None
    q.start(claimed.id, "worker-1")
    q.suspend(claimed.id, "worker-1", reason="pausing")
    result = q.complete(claimed.id, "worker-1")
    assert result.status == Status.COMPLETED
