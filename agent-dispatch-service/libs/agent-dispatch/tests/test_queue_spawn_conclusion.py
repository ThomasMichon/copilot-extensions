"""Import guard for the split-out spawn-conclusion ``TaskQueue`` mixin.

Every method here is already thoroughly exercised through
``agent_dispatch.queue.TaskQueue`` (``test_spawn_reservation.py`` and the
routing/supervisor integration coverage in ``test_routing_provenance.py`` /
``test_supervisor.py``) -- this file only guards that
``agent_dispatch.queue_spawn_conclusion`` remains directly importable with
its own stable public surface, is actually composed into ``TaskQueue``,
and that its record-type annotations resolve cleanly to
``typing.get_type_hints()`` (the same ``queue_schedule_registry.py``
lesson ``test_queue_spawn_reservations.py`` already applies).
"""

from __future__ import annotations

import typing

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_spawn_conclusion import SpawnConclusionMixin, _conclusion_payload


@pytest.mark.guard
def test_task_queue_inherits_the_spawn_conclusion_mixin():
    assert SpawnConclusionMixin in TaskQueue.__mro__


@pytest.mark.guard
def test_spawn_conclusion_methods_are_directly_importable():
    assert callable(SpawnConclusionMixin.record_spawn_conclusion)
    assert callable(SpawnConclusionMixin.claim_spawn_conclusion_retry)
    assert callable(SpawnConclusionMixin.validate_spawn_conclusion_claim)


@pytest.mark.guard
def test_module_level_helpers_are_directly_importable():
    assert callable(_conclusion_payload)


@pytest.mark.guard
def test_mixin_method_annotations_resolve_via_get_type_hints():
    for name in (
        "record_spawn_conclusion",
        "claim_spawn_conclusion_retry",
        "validate_spawn_conclusion_claim",
    ):
        typing.get_type_hints(getattr(SpawnConclusionMixin, name))
