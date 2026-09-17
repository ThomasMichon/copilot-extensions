"""Import guard for the split-out spawn-reservation ``TaskQueue`` mixin.

Every method here is already thoroughly exercised through
``agent_dispatch.queue.TaskQueue`` (``test_spawn_reservation.py``,
``test_spawn_consistency_sweep.py``, and the routing/supervisor
integration coverage in ``test_routing_provenance.py`` /
``test_supervisor.py``) -- this file only guards that
``agent_dispatch.queue_spawn_reservations`` remains directly importable
with its own stable public surface, is actually composed into
``TaskQueue``, and that its record-type annotations resolve cleanly to
``typing.get_type_hints()`` (built with the ``queue_schedule_registry.py``
lesson already applied -- ordinary top-level imports from
``queue_records``, not a ``TYPE_CHECKING``-guarded lazy import -- so this
guard should stay green without ever needing a follow-up fix).
"""

from __future__ import annotations

import typing

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_spawn_reservations import (
    SpawnReservationMixin,
    _conclusion_payload,
    _newer_worktree_reservation,
    _validate_conclusion_claim,
    spawn_key,
)


@pytest.mark.guard
def test_task_queue_inherits_the_spawn_reservation_mixin():
    assert SpawnReservationMixin in TaskQueue.__mro__


@pytest.mark.guard
def test_spawn_reservation_methods_are_directly_importable():
    assert callable(SpawnReservationMixin.reserve_spawn)
    assert callable(SpawnReservationMixin.rearm_spawn)
    assert callable(SpawnReservationMixin.record_spawn)
    assert callable(SpawnReservationMixin.record_spawn_worktree)
    assert callable(SpawnReservationMixin.record_cold)
    assert callable(SpawnReservationMixin.fail_spawn)
    assert callable(SpawnReservationMixin.defer_spawn)
    assert callable(SpawnReservationMixin.retire_spawn)
    assert callable(SpawnReservationMixin.request_spawn_release)
    assert callable(SpawnReservationMixin.settle_spawn)
    assert callable(SpawnReservationMixin.record_spawn_conclusion)
    assert callable(SpawnReservationMixin.claim_spawn_conclusion_retry)
    assert callable(SpawnReservationMixin.validate_spawn_conclusion_claim)


@pytest.mark.guard
def test_module_level_helpers_are_directly_importable():
    assert callable(spawn_key)
    assert callable(_conclusion_payload)
    assert callable(_validate_conclusion_claim)
    assert callable(_newer_worktree_reservation)


@pytest.mark.guard
def test_mixin_method_annotations_resolve_via_get_type_hints():
    """Regression guard mirroring the one added for
    ``queue_schedule_registry.py`` after that module's real
    ``get_type_hints`` finding -- confirms this extraction's ordinary
    top-level ``queue_records`` imports never regress into that failure
    mode."""
    for name in (
        "reserve_spawn",
        "rearm_spawn",
        "_update_reservation",
        "record_spawn",
        "record_spawn_worktree",
        "record_cold",
        "fail_spawn",
        "defer_spawn",
        "retire_spawn",
        "request_spawn_release",
        "settle_spawn",
        "record_spawn_conclusion",
        "claim_spawn_conclusion_retry",
        "validate_spawn_conclusion_claim",
    ):
        typing.get_type_hints(getattr(SpawnReservationMixin, name))
