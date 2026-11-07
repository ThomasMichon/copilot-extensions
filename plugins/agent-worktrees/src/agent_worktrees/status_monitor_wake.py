"""Interruptible sweep waits and hook-triggered wake policy."""

from __future__ import annotations


def _wake_interruptible_wait(wake_event, seconds: float) -> None:
    """Sleep up to ``seconds``, but return immediately (and clear the event)
    if :func:`resident_push.notify` sets ``wake_event`` first.

    This is what turns the periodic sweep interval into a mere **backstop**:
    an explicit status push (or any ``postToolUse`` mutation observed via the
    hook-IPC path) wakes the loop early instead of waiting out the full
    interval, while the interval itself still fires unconditionally for
    every other source of staleness (external git activity, a stale mux
    session, etc.) that has no explicit push of its own.
    """
    if wake_event.wait(timeout=seconds):
        wake_event.clear()


def _kind_wakes_sweep(kind: str, targets: list[str] | None) -> bool:
    """Whether a hook-IPC ``kind`` actually invalidated something and should
    therefore wake the sweep loop early.

    ``postToolUse`` fires for EVERY tool call, but
    ``_ResidentHookPolicy.mutation_targets`` returns an empty list for a
    read-only one (e.g. ``git status``) -- waking the loop for those would
    turn the periodic backstop into a high-frequency render path for zero
    benefit (2026-09-27 Copilot review finding). ``targets is None`` means
    "invalidate everything" (a real, unbounded-scope mutation); a non-empty
    list means "invalidate these specific paths" (also real); an empty list
    means nothing was invalidated at all -- no wake. Pulled out as its own
    pure predicate so the "which kinds push" policy is directly unit-testable
    without needing a live ``HookIpcServer``/``_ResidentHookPolicy.ready()``
    to drive ``_decide`` end-to-end.

    ``handoffWake`` (sent by ``cmd_note_handoff`` via
    ``hook_ipc.send_best_effort`` after it persists a pending handoff) always
    wakes, unconditionally -- it carries no ``targets`` of its own because
    the thing that changed is a handoff record, not a cached status segment;
    the whole point of this kind is "something actionable was just
    registered, stop waiting out the backstop interval for it."
    """
    if kind == "handoffWake":
        return True
    return kind in {"postToolUse", "nativeToolMutation", "nativeToolBatchMutation", "shellToolMutation", "mixedToolBatchMutation"} and (targets is None or bool(targets))
