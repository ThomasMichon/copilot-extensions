"""Durable, per-project handoff-cutover trace store.

``activity.jsonl`` (see ``activity.py``) is a **machine-global rolling log**
with a retention window measured in days -- fine for "what just happened",
wrong for "re-trace this handoff at any time" once the log rotates past a
stage-1 event, which can happen well before a slow-to-audit handoff is ever
looked at again (efforts/active/handoff-cutover-lifecycle-journal/README.md
Phase 3). This module adds an **unrotated, per-worktree sink** that every
stage-mapped ``activity.log_event()`` call also writes to, so a worktree's
full 13-stage handoff history survives indefinitely (until the worktree
itself is reaped) regardless of the rolling log's retention window.

**Namespacing.** A worktree id is only unique *within* a project
(``_find_tracking_file_exact`` in ``__main__.py`` explicitly treats the same
id existing under two projects as an ambiguity error), so this store is
namespaced ``<project>/<worktree-id>.jsonl`` -- never worktree id alone, or
two projects' same-named worktrees would interleave unrelated handoff
attempts in one file.

**Concurrent writers.** Multiple independent processes (the Python CLI, the
launcher's bash/PowerShell hook client, the resident status monitor, and
context-handoff's Node process via ``activity-log``) can append to the same
worktree's trace file concurrently, on both POSIX and Windows. A plain
``O_APPEND`` write is not a documented cross-platform atomicity guarantee, so
each append here takes a real cross-process advisory lock around the write --
mirroring the ``fcntl``/``msvcrt`` pattern already established in
``installer.py``'s ``_binstub_lock`` -- rather than relying on filesystem
append semantics alone.

Writes are **best-effort**: a failure here must never break the lifecycle
event it observes (same contract as ``activity.log_event``), so every public
function swallows its own exceptions.

**Cleanup.** ``remove_trace`` deletes a worktree's trace file (and its lock
companion) and is called from every tracking-record removal path in
``__main__.py`` (mirroring ``disposition_history.remove``), so the "kept
until the worktree is reaped" lifecycle promise actually holds and a reused
worktree id never inherits a stale trace.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config as cfg

# Reject path separators, NUL, and any "." / ".." segment -- both ``project``
# and ``worktree_id`` are copied here without the ``_resolve_worktree_id``
# validation `__main__.py`'s CLI entry points normally apply (``activity-log
# --worktree-id`` and an explicit ``--project`` are accepted as raw strings),
# so a value like ``../../etc`` must not be allowed to escape
# ``cfg.install_dir()/logs/handoff-traces``.
_UNSAFE_COMPONENT = re.compile(r"[/\\\0]|^\.\.?$")


def _validate_component(value: str, label: str) -> str:
    """Reject a path component that could escape the trace root.

    Raises ``ValueError`` on an empty, separator-bearing, NUL-bearing, or
    ``.``/``..`` value. Callers (``append_event``/``read_trace``) catch this
    the same way they catch any other failure -- a write/read against an
    invalid identifier simply no-ops rather than touching an unintended path.
    """
    if not value or _UNSAFE_COMPONENT.search(value):
        raise ValueError(f"invalid {label} for handoff trace path: {value!r}")
    return value


def trace_dir(project: str) -> Path:
    """Root directory holding one project's per-worktree trace files."""
    project = _validate_component(project, "project")
    return cfg.install_dir() / "logs" / "handoff-traces" / project


def trace_path(project: str, worktree_id: str) -> Path:
    """Path to a single worktree's durable, unrotated handoff trace file."""
    worktree_id = _validate_component(worktree_id, "worktree_id")
    return trace_dir(project) / f"{worktree_id}.jsonl"


@contextmanager
def _append_lock(lock_path: Path) -> Iterator[None]:
    """Cross-process exclusive lock guarding one trace file's append.

    Same POSIX/Windows split as ``installer.py``'s ``_binstub_lock``:
    ``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows, both blocking
    (waits for the current holder rather than failing closed) since a
    handoff-stage append is infrequent and cheap -- there is no contention
    hazard worth a non-blocking fast path here.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def append_event(
    project: str | None, worktree_id: str | None, record: dict[str, object]
) -> bool:
    """Append one already-built event record to its durable trace file.

    No-ops (returns False) when ``project`` or ``worktree_id`` is unknown --
    the caller (``activity.log_event``) resolves ``project`` best-effort from
    the in-process active project, which is unset in a few ambient contexts
    (e.g. a bare hook invocation outside any resolved project); those events
    still land in ``activity.jsonl`` as before, just not in this durable
    store. Never raises: a write failure here must not break the lifecycle
    event it observes.
    """
    if not project or not worktree_id:
        return False
    try:
        path = trace_path(project, worktree_id)
        lock_path = path.with_suffix(path.suffix + ".lock")
        line = json.dumps(record, ensure_ascii=True)
        with _append_lock(lock_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return True
    except Exception:
        return False


def read_trace(project: str, worktree_id: str) -> list[dict]:
    """Return every durably-recorded event for one worktree, oldest first.

    Best-effort: a missing file, an invalid ``project``/``worktree_id``, an
    undecodable byte, or an unparseable line is skipped rather than raised,
    matching ``activity.read_events``'s tolerance for a partially written or
    corrupted log. Decoding uses ``errors="replace"`` so one damaged byte
    downgrades to a `\ufffd`-bearing (and thus unparseable, skipped) line
    instead of aborting the whole read via ``UnicodeDecodeError`` -- later
    valid lines in the same file remain readable.
    """
    try:
        path = trace_path(project, worktree_id)
    except ValueError:
        return []
    out: list[dict] = []
    if not path.exists():
        return out
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    out.append(json.loads(raw))
                except Exception:
                    continue
    except OSError:
        return out
    return out


def remove_trace(project: str | None, worktree_id: str | None) -> None:
    """Delete a worktree's durable trace file and its lock companion.

    Best-effort (never raises) -- called wherever a tracking ``<id>.yaml`` is
    unlinked, mirroring ``disposition_history.remove``, so the trace does not
    outlive the worktree it belongs to and a reused worktree id never reads a
    stale predecessor's events.
    """
    if not project or not worktree_id:
        return
    try:
        path = trace_path(project, worktree_id)
    except ValueError:
        return
    for candidate in (path, path.with_suffix(path.suffix + ".lock")):
        try:
            candidate.unlink(missing_ok=True)
        except Exception:
            pass
