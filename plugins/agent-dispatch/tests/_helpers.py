"""Shared test helpers.

``repo`` (the task lane) is a required field on ``create``. The pre-existing
lifecycle tests predate the lane and don't care which repo they run in, so
:class:`RepoDefaultingQueue` defaults ``repo`` to :data:`TEST_REPO` for them.
Repo-scoping tests pass ``repo`` explicitly (an explicit value overrides the
default), and the dedicated "repo is required" test uses the real
``TaskQueue`` directly.
"""

from __future__ import annotations

from typing import Any, Literal, TypeVar, overload

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import CreationOutcome, Task

#: Canonical-remote-shaped lane key used by tests that don't exercise scoping.
TEST_REPO = "example.com/acme/widget"
#: A second lane, for cross-repo isolation tests.
OTHER_REPO = "example.com/acme/gadget"

T = TypeVar("T")


def required(value: T | None) -> T:
    """Make an expected fixture result explicit before inspecting it."""
    assert value is not None
    return value


class RepoDefaultingQueue(TaskQueue):
    """A ``TaskQueue`` whose ``create`` defaults ``repo`` to :data:`TEST_REPO`.

    Lets the lifecycle tests keep calling ``create("title", ...)`` without a
    repo. ``propose`` routes through ``create`` so it inherits the default.
    """

    @overload
    def create(
        self, title: str, *args: Any, repo: str | None = None,
        _with_outcome: Literal[True], **kwargs: Any,
    ) -> CreationOutcome: ...

    @overload
    def create(
        self, title: str, *args: Any, repo: str | None = None,
        _with_outcome: Literal[False] = False, **kwargs: Any,
    ) -> Task: ...

    @overload
    def create(
        self, title: str, *args: Any, repo: str | None = None,
        _with_outcome: bool = False, **kwargs: Any,
    ) -> Task | CreationOutcome: ...

    def create(
        self, title: str, *args: Any, repo: str | None = None, **kwargs: Any,
    ) -> Task | CreationOutcome:
        return super().create(title, *args, repo=repo or TEST_REPO, **kwargs)
