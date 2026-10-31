"""Nonessential creation notices cannot decide transaction outcomes."""

from __future__ import annotations

from collections.abc import Callable
import sys

from . import activity, output


def record_failure(scope: str, error: BaseException, *, worktree_id: str | None = None) -> None:
    """Retain sanitized failure evidence through the existing activity boundary."""
    fields: dict[str, object] = {"scope": scope, "error_type": type(error).__name__}
    for attribute, field in (("errno", "os_errno"), ("winerror", "winerror")):
        value = getattr(error, attribute, None)
        if type(value) is int:
            fields[field] = value
    activity.log_event("creation_diagnostic", worktree_id=worktree_id, **fields)


def emit(scope: str, write: Callable[[], object], *, worktree_id: str | None = None) -> None:
    """Attempt one advisory emission; output failure does not change owner work."""
    try:
        write()
    except OSError as error:
        record_failure(scope, error, worktree_id=worktree_id)


class _CreationNoticeStream:
    def __init__(self, stream, worktree_id: str | None):
        self.stream = stream
        self.worktree_id = worktree_id

    def write(self, text):
        emit("creation_progress", lambda: self.stream.write(text), worktree_id=self.worktree_id)

    def flush(self):
        emit("creation_progress", self.stream.flush, worktree_id=self.worktree_id)


def human_operation(label: str, *, enabled: bool, worktree_id: str | None):
    """Keep scoped progress framing and cleanup, with advisory stream I/O only."""
    if output.HumanOperation is None or not enabled:
        return output.human_operation(label, enabled=enabled)
    parent = output.current_operation()
    stream = parent.stream if parent is not None else sys.stderr
    return output.HumanOperation(label, stream=_CreationNoticeStream(stream, worktree_id))
