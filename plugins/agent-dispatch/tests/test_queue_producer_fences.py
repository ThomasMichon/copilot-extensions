"""Import guard for the split-out producer-fence ``TaskQueue`` mixin.

Every method here is already thoroughly exercised through
``agent_dispatch.queue.TaskQueue`` (``test_producer_fences.py``) -- this file
only guards that ``agent_dispatch.queue_producer_fences`` remains directly
importable with its own stable public surface, is actually composed into
``TaskQueue`` (rather than merely defined alongside it), that
``agent_dispatch.queue`` still re-exports the four names
(``ProducerFenceError``/``ProducerScopeValidationError``/
``ProducerScopeState``/``ProducerScopeTransition``) existing
``from agent_dispatch.queue import ...`` consumers depend on, and that its
annotations resolve cleanly to ``typing.get_type_hints()`` (this module was
built with the ``queue_schedule_registry.py`` lesson already applied --
``Status``/``TaskError`` are ordinary top-level imports from the
dependency-free ``queue_records.py``, not a ``TYPE_CHECKING``-guarded lazy
import, so this guard should stay green without ever needing a follow-up
fix).
"""

from __future__ import annotations

import typing

import pytest

from agent_dispatch.queue import (
    ProducerFenceError,
    ProducerScopeState,
    ProducerScopeTransition,
    ProducerScopeValidationError,
    TaskQueue,
)
from agent_dispatch.queue_producer_fences import (
    ProducerFenceMixin,
)
from agent_dispatch.queue_producer_fences import ProducerFenceError as _MixinFenceError
from agent_dispatch.queue_producer_fences import (
    ProducerScopeState as _MixinScopeState,
)
from agent_dispatch.queue_producer_fences import (
    ProducerScopeTransition as _MixinScopeTransition,
)
from agent_dispatch.queue_producer_fences import (
    ProducerScopeValidationError as _MixinScopeValidationError,
)
from tests._helpers import TEST_REPO


@pytest.mark.guard
def test_task_queue_inherits_the_producer_fence_mixin():
    assert ProducerFenceMixin in TaskQueue.__mro__


@pytest.mark.guard
def test_queue_re_exports_match_the_mixin_module():
    """``agent_dispatch.queue``'s re-exports must be the *same* objects
    ``queue_producer_fences.py`` defines, not merely same-named -- existing
    ``except ProducerFenceError`` call sites in ``coordinator.py``/
    ``mcp_http.py`` depend on `is`-identity across both import paths."""
    assert ProducerFenceError is _MixinFenceError
    assert ProducerScopeValidationError is _MixinScopeValidationError
    assert ProducerScopeState is _MixinScopeState
    assert ProducerScopeTransition is _MixinScopeTransition


@pytest.mark.guard
def test_producer_fence_methods_are_directly_importable():
    assert callable(ProducerFenceMixin._validate_producer_token)
    assert callable(ProducerFenceMixin._validate_producer_scope)
    assert callable(ProducerFenceMixin._validate_required_label)
    assert callable(ProducerFenceMixin._validate_producer_capability)
    assert callable(ProducerFenceMixin._capability_hash)
    assert callable(ProducerFenceMixin._normalize_producer_fence)
    assert callable(ProducerFenceMixin._producer_request_hash)
    assert callable(ProducerFenceMixin._producer_scope_row)
    assert callable(ProducerFenceMixin._required_label_scope_rows)
    assert callable(ProducerFenceMixin._task_fence_matches_scope)
    assert callable(ProducerFenceMixin._claim_fence_rejection)
    assert callable(ProducerFenceMixin._scope_blockers)
    assert callable(ProducerFenceMixin._record_claim_rejection)
    assert callable(ProducerFenceMixin._producer_scope_state_from_conn)
    assert callable(ProducerFenceMixin.producer_scope_status)
    assert callable(ProducerFenceMixin.handoff_producer_scope)


@pytest.mark.guard
def test_mixin_method_annotations_resolve_via_get_type_hints():
    """Regression guard mirroring the ones added for
    ``queue_schedule_registry.py``/``queue_spawn_reservations.py`` after
    those modules' real ``get_type_hints`` finding -- confirms this newer
    mixin's ordinary top-level imports never regress into the same failure
    mode. Also covers the one lazy, function-local import in this module
    (``_scope_blockers``'s import of ``queue.py``'s ``_TASK_BULK_SELECT``),
    which is a plain runtime string constant, never used in a type
    annotation, so it carries none of that gap."""
    for name in (
        "_validate_producer_token",
        "_validate_producer_scope",
        "_validate_required_label",
        "_validate_producer_capability",
        "_capability_hash",
        "_normalize_producer_fence",
        "_producer_request_hash",
        "_producer_scope_row",
        "_required_label_scope_rows",
        "_task_fence_matches_scope",
        "_claim_fence_rejection",
        "_scope_blockers",
        "_record_claim_rejection",
        "_producer_scope_state_from_conn",
        "producer_scope_status",
        "handoff_producer_scope",
    ):
        typing.get_type_hints(getattr(ProducerFenceMixin, name))


def test_mixin_methods_work_end_to_end(tmp_path):
    """Exercise the mixin's producer-scope handoff and status lookup against
    a real ``TaskQueue`` so the top-level imports are proven to resolve
    correctly at runtime, not just that the names are importable in
    isolation. Full behavioral coverage (fence rejection, claim-time
    blocking, replay semantics) already exists in ``test_producer_fences.py``
    and needs no changes."""
    q = TaskQueue(str(tmp_path / "q.sqlite3"))

    status = q.producer_scope_status(TEST_REPO, "scheduled")
    assert status.managed is False

    transition = q.handoff_producer_scope(
        TEST_REPO,
        "scheduled",
        producer_id="producer-a",
        expected_generation=0,
        required_label="nightly",
    )
    assert transition.replayed is False
    assert transition.producer_capability is not None
    assert transition.state.managed is True
    assert transition.state.current_generation == 1
    assert transition.state.active_producer == "producer-a"

    replay = q.handoff_producer_scope(
        TEST_REPO,
        "scheduled",
        producer_id="producer-a",
        expected_generation=0,
        required_label="nightly",
    )
    assert replay.replayed is True

    status_after = q.producer_scope_status(TEST_REPO, "scheduled")
    assert status_after.current_generation == 1
    assert status_after.active_producer == "producer-a"
