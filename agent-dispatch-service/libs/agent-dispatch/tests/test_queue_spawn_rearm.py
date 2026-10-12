"""Import guard for the split-out ``rearm_spawn`` mixin.

Behavioral coverage already lives in ``test_spawn_reservation.py`` and
``test_doctor.py``/``test_reattach.py``'s release-requested paths; this
file only guards direct importability, ``TaskQueue`` composition, and
runtime-resolvable annotations for the extracted mixin (mirroring
``test_queue_excludes.py``'s pattern).
"""

from __future__ import annotations

import typing

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_spawn_rearm import SpawnRearmMixin


@pytest.mark.guard
def test_task_queue_inherits_the_spawn_rearm_mixin():
    assert SpawnRearmMixin in TaskQueue.__mro__


@pytest.mark.guard
def test_rearm_spawn_is_directly_importable():
    assert callable(SpawnRearmMixin.rearm_spawn)


@pytest.mark.guard
def test_rearm_spawn_annotations_resolve_via_get_type_hints():
    typing.get_type_hints(SpawnRearmMixin.rearm_spawn)
