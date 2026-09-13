"""Import guard for the split-out schedule-registry / supervisor-registration
/ schedule-lease / resource-reservation ``TaskQueue`` mixin.

Every method here is already thoroughly exercised through
``agent_dispatch.queue.TaskQueue`` (``test_schedule_registry.py``,
``test_registrations.py``, and the resource-reservation coverage in
``test_producers_emitter.py`` / ``test_coordinator.py``) -- this file only
guards that ``agent_dispatch.queue_schedule_registry`` remains directly
importable with its own stable public surface, is actually composed into
``TaskQueue`` (rather than merely defined alongside it), and that its
lazy, function-local ``from .queue import ...`` calls (used to avoid a real
circular import at module-load time, since ``queue.py`` imports this
module's mixin class to inherit from) resolve correctly once both modules
have finished importing.
"""

from __future__ import annotations

from agent_dispatch.queue import ResourceReservation, ScheduleLease, ScheduleRecord, TaskQueue
from agent_dispatch.queue_schedule_registry import ScheduleRegistrationMixin


def test_task_queue_inherits_the_schedule_registration_mixin():
    assert ScheduleRegistrationMixin in TaskQueue.__mro__


def test_schedule_registry_methods_are_directly_importable():
    assert callable(ScheduleRegistrationMixin.register_schedule)
    assert callable(ScheduleRegistrationMixin.list_schedules)
    assert callable(ScheduleRegistrationMixin.get_schedule)
    assert callable(ScheduleRegistrationMixin.remove_schedule)
    assert callable(ScheduleRegistrationMixin.set_schedule_paused)


def test_supervisor_registration_methods_are_directly_importable():
    assert callable(ScheduleRegistrationMixin.register_registration)
    assert callable(ScheduleRegistrationMixin.list_registrations)
    assert callable(ScheduleRegistrationMixin.get_registration)
    assert callable(ScheduleRegistrationMixin.remove_registration)
    assert callable(ScheduleRegistrationMixin.set_registration_status)


def test_schedule_lease_methods_are_directly_importable():
    assert callable(ScheduleRegistrationMixin.acquire_schedule_lease)
    assert callable(ScheduleRegistrationMixin.release_schedule_lease)
    assert callable(ScheduleRegistrationMixin.get_schedule_lease)
    assert callable(ScheduleRegistrationMixin.list_schedule_leases)


def test_resource_reservation_methods_are_directly_importable():
    assert callable(ScheduleRegistrationMixin.acquire_resource_reservation)
    assert callable(ScheduleRegistrationMixin.bind_resource_reservation)
    assert callable(ScheduleRegistrationMixin.release_resource_reservation)
    assert callable(ScheduleRegistrationMixin.list_resource_reservations)


def test_mixin_methods_resolve_the_lazy_queue_record_imports(tmp_path):
    """Exercise one method from each of the four sub-clusters against a real
    ``TaskQueue`` so the lazy, function-local ``from .queue import ...``
    calls each method makes are proven to resolve their record dataclasses
    correctly, not just that the names are importable in isolation."""
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
