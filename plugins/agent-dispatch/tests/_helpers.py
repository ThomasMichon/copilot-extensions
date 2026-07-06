"""Shared test helpers.

``repo`` (the task lane) is a required field on ``create``. The pre-existing
lifecycle tests predate the lane and don't care which repo they run in, so
:class:`RepoDefaultingQueue` defaults ``repo`` to :data:`TEST_REPO` for them.
Repo-scoping tests pass ``repo`` explicitly (an explicit value overrides the
default), and the dedicated "repo is required" test uses the real
``TaskQueue`` directly.
"""

from __future__ import annotations

from agent_dispatch.queue import TaskQueue

#: Canonical-remote-shaped lane key used by tests that don't exercise scoping.
TEST_REPO = "example.com/acme/widget"
#: A second lane, for cross-repo isolation tests.
OTHER_REPO = "example.com/acme/gadget"


class RepoDefaultingQueue(TaskQueue):
    """A ``TaskQueue`` whose ``create`` defaults ``repo`` to :data:`TEST_REPO`.

    Lets the lifecycle tests keep calling ``create("title", ...)`` without a
    repo. ``propose`` routes through ``create`` so it inherits the default.
    """

    def create(self, *args, repo=None, **kwargs):  # type: ignore[override]
        return super().create(*args, repo=repo or TEST_REPO, **kwargs)
