"""Import guard for the split-out ``clear_exclude`` mixin.

Split out of :mod:`agent_dispatch.queue_lifecycle` for module-size discipline
(mirroring ``test_queue_suspend.py``'s / ``test_queue_lifecycle.py``'s own
pattern). Behavioral coverage already lives in ``test_queue.py``; this file
only guards direct importability, ``TaskQueue`` composition, and
runtime-resolvable annotations for the extracted mixin.
"""

from __future__ import annotations

import typing

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_excludes import QueueExcludeMixin


@pytest.mark.guard
def test_task_queue_inherits_the_exclude_mixin():
    assert QueueExcludeMixin in TaskQueue.__mro__


@pytest.mark.guard
def test_exclude_methods_are_directly_importable():
    assert callable(QueueExcludeMixin.clear_exclude)


@pytest.mark.guard
def test_exclude_annotations_resolve_via_get_type_hints():
    typing.get_type_hints(QueueExcludeMixin.clear_exclude)
