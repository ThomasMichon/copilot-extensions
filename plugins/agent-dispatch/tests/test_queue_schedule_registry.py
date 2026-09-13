"""Import guard for the split-out schedule-registry / supervisor-registration
/ schedule-lease / resource-reservation ``TaskQueue`` mixin.

Every method here is already thoroughly exercised through
``agent_dispatch.queue.TaskQueue`` (``test_schedule_registry.py``,
``test_registrations.py``, and the resource-reservation coverage in
``test_producers_emitter.py`` / ``test_coordinator.py``) -- this file only
guards that ``agent_dispatch.queue_schedule_registry`` remains directly
importable with its own stable public surface, is actually composed into
``TaskQueue`` (rather than merely defined alongside it), and that its record
dataclasses (``agent_dispatch.queue_records``, re-exported from ``queue``
for existing call sites) resolve correctly end-to-end.
"""

from __future__ import annotations

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import ResourceReservation, ScheduleLease, ScheduleRecord
from agent_dispatch.queue_schedule_registry import ScheduleRegistrationMixin


@pytest.mark.guard
def test_task_queue_inherits_the_schedule_registration_mixin():
    assert ScheduleRegistrationMixin in TaskQueue.__mro__


@pytest.mark.guard
def test_schedule_registry_methods_are_directly_importable():
    assert callable(ScheduleRegistrationMixin.register_schedule)
    assert callable(ScheduleRegistrationMixin.list_schedules)
    assert callable(ScheduleRegistrationMixin.get_schedule)
    assert callable(ScheduleRegistrationMixin.remove_schedule)
    assert callable(ScheduleRegistrationMixin.set_schedule_paused)


@pytest.mark.guard
def test_supervisor_registration_methods_are_directly_importable():
    assert callable(ScheduleRegistrationMixin.register_registration)
    assert callable(ScheduleRegistrationMixin.list_registrations)
    assert callable(ScheduleRegistrationMixin.get_registration)
    assert callable(ScheduleRegistrationMixin.remove_registration)
    assert callable(ScheduleRegistrationMixin.set_registration_status)


@pytest.mark.guard
def test_schedule_lease_methods_are_directly_importable():
    assert callable(ScheduleRegistrationMixin.acquire_schedule_lease)
    assert callable(ScheduleRegistrationMixin.release_schedule_lease)
    assert callable(ScheduleRegistrationMixin.get_schedule_lease)
    assert callable(ScheduleRegistrationMixin.list_schedule_leases)


@pytest.mark.guard
def test_resource_reservation_methods_are_directly_importable():
    assert callable(ScheduleRegistrationMixin.acquire_resource_reservation)
    assert callable(ScheduleRegistrationMixin.bind_resource_reservation)
    assert callable(ScheduleRegistrationMixin.release_resource_reservation)
    assert callable(ScheduleRegistrationMixin.list_resource_reservations)


def test_mixin_methods_resolve_their_record_dataclasses_end_to_end(tmp_path):
    """Exercise one method from each of the four sub-clusters against a real
    ``TaskQueue`` so the top-level ``queue_records`` imports each method uses
    are proven to resolve correctly at runtime, not just that the names are
    importable in isolation."""
    q = TaskQueue(str(tmp_path / "q.sqlite3"))

    record = q.register_schedule(
        {
            "id": "nightly",
            "title": "t",
            "prompt": "p",
            "repo": "owner/repo",
            "interval_seconds": 3600,
        }
    )
    assert isinstance(record, ScheduleRecord)

    reg = q.register_registration("schedule", {"id": "nightly", "repo": "owner/repo"})
    assert reg.kind == "schedule"

    lease, granted = q.acquire_schedule_lease("scope-a", "holder-1")
    assert isinstance(lease, ScheduleLease)
    assert granted is True

    reservation, granted = q.acquire_resource_reservation("res-a", "owner-1", ttl=60)
    assert isinstance(reservation, ResourceReservation)
    assert granted is True


@pytest.mark.guard
def test_mixin_method_annotations_resolve_via_get_type_hints():
    """Regression guard for a real finding from this module's own PR review:
    an earlier version of this mixin satisfied static analysis with a
    ``TYPE_CHECKING``-only import of the record dataclasses, which left them
    unresolvable to runtime introspection (``typing.get_type_hints`` raised
    ``NameError`` for every annotated method) since they were never actually
    bound in the module's real globals. Now that
    ``queue_schedule_registry`` imports them as ordinary top-level names
    from ``queue_records``, every annotated method must resolve cleanly."""
    import typing

    for name in (
        "register_schedule",
        "list_schedules",
        "get_schedule",
        "set_schedule_paused",
        "acquire_schedule_lease",
        "get_schedule_lease",
        "list_schedule_leases",
        "acquire_resource_reservation",
        "bind_resource_reservation",
        "list_resource_reservations",
    ):
        typing.get_type_hints(getattr(ScheduleRegistrationMixin, name))
