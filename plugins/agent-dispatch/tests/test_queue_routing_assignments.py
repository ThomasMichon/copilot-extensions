"""Import guard for the split-out routing-assignment ``TaskQueue`` mixin.

Every method here is already thoroughly exercised through
``agent_dispatch.queue.TaskQueue`` (``test_routing_provenance.py``) --
this file only guards that ``agent_dispatch.queue_routing_assignments``
remains directly importable with its own stable public surface, is
actually composed into ``TaskQueue`` (rather than merely defined
alongside it), and that its record-type annotations resolve cleanly to
``typing.get_type_hints()`` (this module was built with the lesson from
``queue_schedule_registry.py``'s original review finding already applied
-- ordinary top-level imports of its shared record types, not a
``TYPE_CHECKING``-guarded lazy import -- so this guard should stay green
without ever needing a follow-up fix).
"""

from __future__ import annotations

import typing

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_routing_assignments import RoutingAssignmentMixin
from tests._helpers import TEST_REPO


@pytest.mark.guard
def test_task_queue_inherits_the_routing_assignment_mixin():
    assert RoutingAssignmentMixin in TaskQueue.__mro__


@pytest.mark.guard
def test_routing_assignment_methods_are_directly_importable():
    assert callable(RoutingAssignmentMixin.record_routing_assignment)
    assert callable(RoutingAssignmentMixin.transition_routing_assignment)
    assert callable(RoutingAssignmentMixin.record_routing_billing_ref)
    assert callable(RoutingAssignmentMixin.get_routing_assignment)
    assert callable(RoutingAssignmentMixin.list_routing_assignments)
    assert callable(RoutingAssignmentMixin.routing_assignment_events)


@pytest.mark.guard
def test_mixin_method_annotations_resolve_via_get_type_hints():
    """Regression guard mirroring the one added for
    ``queue_schedule_registry.py`` after that module's real
    ``get_type_hints`` finding -- confirms this newer mixin's ordinary
    top-level imports never regress into the same failure mode."""
    for name in (
        "record_routing_assignment",
        "transition_routing_assignment",
        "record_routing_billing_ref",
        "get_routing_assignment",
        "list_routing_assignments",
        "routing_assignment_events",
    ):
        typing.get_type_hints(getattr(RoutingAssignmentMixin, name))


def test_mixin_methods_work_end_to_end(tmp_path):
    """Exercise the mixin's full assignment lifecycle against a real
    ``TaskQueue`` so the top-level imports are proven to resolve correctly
    at runtime, not just that the names are importable in isolation."""
    q = TaskQueue(str(tmp_path / "q.sqlite3"))
    task = q.create(title="t", prompt="p", repo=TEST_REPO)
    reservation, _ = q.reserve_spawn(task.id)

    assignment, created = q.record_routing_assignment(
        reservation.key,
        {
            "purpose": "coding",
            "selected_model": "gpt-test",
            "eligibility_state": "demonstrated",
            "selection_reason": "lowest-cost-demonstrated",
            "execution_surface": "restricted-container",
            "decision_ref": "decision:sha256:abc123",
            "containment_profile_ref": "restricted-code",
            "coordinator_session_ref": "session:coordinator-1",
        },
    )
    assert created is True
    assert assignment.state == "assigned"
    assert q.get_routing_assignment(reservation.key) is not None
    assert assignment in q.list_routing_assignments(task_id=task.id)

    admitted, transitioned = q.transition_routing_assignment(
        reservation.key, "admitted", "coordinator"
    )
    assert transitioned is True
    assert admitted.state == "admitted"

    linked = q.record_routing_billing_ref(
        reservation.key, "evt-1", "provider-x", "ref-1", "coordinator"
    )
    assert linked is True

    events = q.routing_assignment_events(reservation.key)
    assert any(e["event_type"] == "billing-linked" for e in events)
