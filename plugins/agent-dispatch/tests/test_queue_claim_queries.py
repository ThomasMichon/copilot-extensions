"""Import guard for the split-out claim/query ``TaskQueue`` mixin.

Behavioral coverage already lives in ``test_queue.py`` and the claim/query
integration suites. This file only guards direct importability, ``TaskQueue``
composition, MRO ordering against the storage mixin it depends on, and
runtime-resolvable annotations for the extracted mixin.
"""

from __future__ import annotations

import typing

import pytest

from agent_dispatch.queue import ClaimOutcome, TaskQueue
from agent_dispatch.queue_claim_queries import QueueClaimQueriesMixin
from agent_dispatch.queue_common import ClaimOutcome as _CommonClaimOutcome
from agent_dispatch.queue_storage import QueueStorageMixin


@pytest.mark.guard
def test_task_queue_inherits_the_claim_query_mixin():
    assert QueueClaimQueriesMixin in TaskQueue.__mro__


@pytest.mark.guard
def test_queue_re_exports_match_claim_query_dependencies():
    assert ClaimOutcome is _CommonClaimOutcome


@pytest.mark.guard
def test_claim_query_mixin_methods_are_directly_importable():
    assert callable(QueueClaimQueriesMixin.claim_one)
    assert callable(QueueClaimQueriesMixin.claim_outcome)
    assert callable(QueueClaimQueriesMixin.mine)
    assert callable(QueueClaimQueriesMixin._affinity_score)
    assert callable(QueueClaimQueriesMixin.get)
    assert callable(QueueClaimQueriesMixin.has_pending_wakes)
    assert callable(QueueClaimQueriesMixin.list)
    assert callable(QueueClaimQueriesMixin.find)
    assert callable(QueueClaimQueriesMixin.sweep)
    assert callable(QueueClaimQueriesMixin.events)
    assert callable(QueueClaimQueriesMixin.progress_log)
    assert callable(QueueClaimQueriesMixin.get_reservation)
    assert callable(QueueClaimQueriesMixin.latest_reservation)
    assert callable(QueueClaimQueriesMixin.list_reservations)


@pytest.mark.guard
def test_claim_query_mixin_order_follows_storage_in_task_queue_mro():
    mro = TaskQueue.__mro__
    assert mro.index(QueueStorageMixin) < mro.index(QueueClaimQueriesMixin)


@pytest.mark.guard
def test_claim_query_annotations_resolve_via_get_type_hints():
    for name in (
        "claim_one",
        "claim_outcome",
        "mine",
        "_affinity_score",
        "get",
        "has_pending_wakes",
        "list",
        "find",
        "sweep",
        "events",
        "progress_log",
        "get_reservation",
        "latest_reservation",
        "list_reservations",
    ):
        typing.get_type_hints(getattr(QueueClaimQueriesMixin, name))


def test_claim_query_mixin_methods_work_end_to_end(tmp_path):
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    first = q.create("claim me", repo="owner/repo")
    second = q.create("find me", repo="owner/repo", prompt="needle")

    claimed = q.claim_outcome("worker-1", repo="owner/repo", task_id=first.id)
    inbox = q.mine("host-a", "wt-1", repo="owner/repo")
    found = q.find("needle", repo="owner/repo")
    listed = q.list(repo="owner/repo")
    sweep = q.sweep(repo="owner/repo")

    assert isinstance(claimed, ClaimOutcome)
    assert claimed.task is not None and claimed.task.id == first.id
    assert inbox == {"assigned": [], "owned": []}
    assert [task.id for task in found] == [second.id]
    assert {task.id for task in listed} == {first.id, second.id}
    assert {task.id for task in sweep} == {first.id, second.id}
