"""Call-scoped subject evidence from the renderer that produced a segment."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class SegmentSubject:
    project: str
    path: str
    worktree_id: str
    creation_nonce: str


@dataclass(frozen=True)
class RenderedSegment:
    subject: SegmentSubject
    value: str


@dataclass
class RenderCapture:
    completed: RenderedSegment | None = None
    count: int = 0


_capture: ContextVar[RenderCapture | None] = ContextVar(
    "worktrees_segment_render_capture", default=None,
)


@contextmanager
def capture_render() -> Iterator[RenderCapture]:
    capture = RenderCapture()
    token = _capture.set(capture)
    try:
        yield capture
    finally:
        _capture.reset(token)


def is_capturing() -> bool:
    return _capture.get() is not None


def render_subject(
    project: str, path: str | None, worktree_id: object, creation_nonce: object,
) -> SegmentSubject | None:
    if (
        not project or not path or not isinstance(worktree_id, str) or not worktree_id
        or not isinstance(creation_nonce, str)
    ):
        return None
    return SegmentSubject(
        project, os.path.normcase(os.path.realpath(path)), worktree_id, creation_nonce,
    )


def record_render(subject: SegmentSubject | None, value: str) -> str:
    capture = _capture.get()
    if capture is not None:
        capture.count += 1
        capture.completed = (
            RenderedSegment(subject, value) if subject is not None and capture.count == 1
            else None
        )
    return value
