"""The shared "spawn a worker, post its outcome to the Inbox" lifecycle.

See ``inbox.py``'s module docstring for the larger picture: before it,
each call site that spawned a background thread (``_run_bg``, the
setup-reload worker, ...) hand-rolled its own thread + marshalling +
cancellation/error bookkeeping. ``run_background`` is the ONE
implementation of that "spawn + marshal the outcome back" shape now --
``PickerScreen._run_bg`` is a thin, signature-preserving wrapper around it
so its callers needed zero changes.

Not every producer goes through ``run_background``: a worker that
already runs on its own thread for other reasons (one already spawned via
``threading.Thread``/``LiveLoader``, a setup/reload collect pass, ...) --
``_apply_from_worker``'s callers among them -- posts its outcome directly
into the owning screen's ``Inbox`` instead of being spawned by, or routed
through, this module at all. ``Inbox.post()`` is what both shapes have in
common; ``run_background`` is only the shared *spawn* lifecycle for the
callers that need one.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable
from uuid import uuid4

log = logging.getLogger("agent-worktrees.picker")


def run_background(
    *,
    inbox,
    bg_threads: set[threading.Thread],
    cancel: threading.Event,
    label: str,
    work: Callable[[], object],
    done: Callable[[object], object] | None = None,
    quiet: bool = False,
    get_busy_label: Callable[[], str | None] | None = None,
    set_busy_label: Callable[[str | None], None] | None = None,
    set_debug: Callable[[str], None] | None = None,
    thread_name_prefix: str = "bg",
) -> None:
    """Run *work* on a daemon thread; apply its outcome via *inbox*.

    ``work()`` runs off-thread. Its outcome (a result or an exception) is
    posted into *inbox* as a single zero-argument closure under a fresh,
    unique slot -- never applied inline on the worker thread, never marshalled
    via a hand-rolled ``app.call_from_thread`` at the call site. The screen's
    own inbox drain (its render tick and/or ``InboxUpdated`` handler) invokes
    that closure on the event-loop thread, so no widget is ever mutated
    off-thread and a burst of concurrent workers' outcomes land in one
    batched drain rather than N separate event-loop re-entries.

    Mirrors the exact contract ``PickerScreen._run_bg`` documented: unless
    ``quiet``, *label* is shown as the busy indicator for the duration (via
    ``set_busy_label``) and cleared on completion; an exception from
    ``work()`` surfaces via ``set_debug`` (truncated to one line) unless
    ``quiet``, in which case ``done(None)`` still runs so the caller can
    always finalize; an exception from ``done()`` itself is also surfaced via
    ``set_debug`` rather than propagating. The worker is tracked in
    *bg_threads* and honors *cancel*: a worker whose outcome arrives after
    the owning screen already tore down (``cancel`` set) drops its result
    quietly instead of posting into a now-abandoned inbox.

    Returns immediately.
    """
    if not quiet and set_busy_label is not None:
        set_busy_label(label)

    def _worker() -> None:
        try:
            result, err = work(), None
        except Exception as exc:  # a worker thread must never die silently
            result, err = None, exc

        if cancel.is_set():
            log.debug(
                "background action %r: owner torn down while this worker "
                "was still running; dropping its outcome (err=%r)",
                label, err,
            )
            return

        def _apply() -> None:
            if not quiet and set_busy_label is not None:
                set_busy_label(None)
            if err is not None:
                if quiet:
                    if done is not None:
                        try:
                            done(None)
                        except Exception:
                            pass
                elif set_debug is not None:
                    detail = str(err).strip()
                    detail = detail.splitlines()[0] if detail else type(err).__name__
                    set_debug(f"{label} failed · {detail[:80]}")
            elif done is not None:
                try:
                    done(result)
                except Exception as exc:
                    if set_debug is not None:
                        set_debug(f"{label} · applied with error: {str(exc)[:60]}")

        # ``Inbox.post`` never raises (it logs and swallows a wake failure
        # itself), so the outcome is always at least recorded even if the
        # owning render flow is torn down or unreachable.
        inbox.post(f"{thread_name_prefix}-result:{uuid4().hex}", _apply)

    def _tracked_worker() -> None:
        thread = threading.current_thread()
        try:
            _worker()
        finally:
            bg_threads.discard(thread)

    thread = threading.Thread(
        target=_tracked_worker, name=f"{thread_name_prefix}:{label}", daemon=True
    )
    bg_threads.add(thread)
    thread.start()
