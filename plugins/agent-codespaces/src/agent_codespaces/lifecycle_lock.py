"""The widened SSH target-lock seam for destructive CodeSpace operations.

Split out of ``__main__.py`` (module-size budget). session-rescue-parity
Phase 1's recorded lock-widening decision: a destructive caller (stop/
finalize/delete/prune/reclaim) must hold ``ssh_manager``'s per-target
``TargetLock`` across its **entire** sync-then-act sequence, not only
``sync_codespace_sessions()``'s own internal sync sub-step -- otherwise a
concurrent capture (``agent-codespaces sync-sessions``, Phase 3) can pass
its liveness probe and pull in the window between "sync finished" and "the
destructive action actually ran/completed".

Does **not** protect against a truly external actor (a human running
``gh codespace stop`` directly, or GitHub's own idle-timeout) bypassing
this repo's lock entirely -- an accepted residual risk, identical in kind
to a container being ``docker stop``'d by a process outside
``agent-containers``' own lifecycle code.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def lifecycle_lock(
    name: str, *, refuse_live_users: str | None = None, force: bool = False,
) -> Iterator[object]:
    """Acquire (and always release) the widened lock for *name*.

    Yields the acquired ``ssh_manager.TargetLock``, which a caller passes
    straight through to ``sync_codespace_sessions(..., lock=...)`` so that
    call reuses it instead of acquiring/releasing its own. Raises
    ``ssh_manager.TargetBusyError`` unchanged when another process already
    holds it -- each caller decides how to report that (print + exit 1,
    skip this pass, emit a modal error frame, etc).

    ``refuse_live_users`` (the operation's name, e.g. ``"delete"``): once the
    lock is held, also refuse when any other live local process still rides the
    box -- a detached SSH ControlMaster, forward, or ``gh codespace ssh`` that
    outlived the lock writer -- by raising ``live_users.CodespaceInUseError`` (a
    ``TargetBusyError``). ``force`` skips that check (not the lock itself).
    """
    from ssh_manager import TargetLock

    lock = TargetLock(name, op="codespace-lifecycle")
    lock.acquire(force=False)
    try:
        if refuse_live_users and not force:
            from .live_users import refuse_if_in_use

            refuse_if_in_use(name, refuse_live_users)
        yield lock
    finally:
        lock.release()
