#!/usr/bin/env python3
'''"Launch in new window": open a brand-new, VISIBLE terminal window running
a worktree's launch, WITHOUT exiting the Picker.

A standalone module (rather than a method on
``PickerScreenWorktreeActionsMixin`` in ``engine_worktree_actions.py``)
purely to keep that already-large mixin file under this repo's flat
1000-line module-size cap. No functional reason to split otherwise --
``open_worktree_cli_headed`` takes the ``PickerScreen`` instance explicitly
instead of being a method on it.

"Launch in new window" is a launch-plan MODIFIER composed with the row's
existing Open/Resume decision (``_run_launch`` with
``LaunchRequest.new_window=True``), not a separate verb -- the identical
launch-plan execution (and its mux-daemon registration) runs either way.
'''
from __future__ import annotations

import contextlib
import io
import sys
import threading

# ---------------------------------------------------------------------------
# Thread-scoped stdout capture
# ---------------------------------------------------------------------------
# `_run_launch` and the functions it calls are CLI-shaped: they `print()`
# their own error messages rather than returning them. Calling it in-process,
# on a background thread, WHILE the Textual TUI still owns the terminal
# (unlike every other `_run_launch` call site, which only ever runs after
# `app.exit()` has torn the TUI down) means a stray `print()` would corrupt
# the live render instead of being invisible.
#
# A plain `contextlib.redirect_stdout` swaps `sys.stdout` for the WHOLE
# process, not just this one daemon thread -- a concurrent write from the
# Textual render loop or another `_run_bg` worker would also land in this
# capture buffer (and be misreported as THIS action's error), and two
# concurrent "Launch in new window" calls would clobber each other's
# redirect. The nested, refcounted install below gives each call its OWN
# thread-local sink while sharing one proxy instance, and restores
# `sys.stdout` to whatever it was immediately before the FIRST concurrent
# caller once the LAST one exits -- properly nestable with anything else
# that also swaps `sys.stdout` around this call (e.g. pytest's own per-test
# `capsys`/`capfd`, which installs and restores its own proxy per test; an
# "install once, never restore" version breaks the moment that teardown
# puts a DIFFERENT object back in `sys.stdout` between tests).
_stdout_lock = threading.Lock()
_active_captures = 0
_prior_stdout = None
_thread_sinks = threading.local()


class _ThreadScopedStdout:
    def write(self, text):
        sink = getattr(_thread_sinks, "sink", None)
        (sink or _prior_stdout).write(text)

    def flush(self):
        _prior_stdout.flush()


_proxy = _ThreadScopedStdout()


@contextlib.contextmanager
def _capture_stdout_for_this_thread():
    global _active_captures, _prior_stdout
    with _stdout_lock:
        if _active_captures == 0:
            _prior_stdout = sys.stdout
            sys.stdout = _proxy
        _active_captures += 1
    sink = io.StringIO()
    _thread_sinks.sink = sink
    try:
        yield sink
    finally:
        _thread_sinks.sink = None
        with _stdout_lock:
            _active_captures -= 1
            if _active_captures == 0:
                sys.stdout = _prior_stdout
                _prior_stdout = None


def open_worktree_cli_headed(screen, rec, *, no_mux: bool = False, ahp: bool = False) -> None:
    """Only offered (see ``_session_action_verbs``) for a local,
    mux-live-or-resumable row, so no remote/SSH branch is needed here.
    Reuses the row's ordinary resume decision (the same one Open/Resume
    would dispatch) -- forwarding the submenu's own ``no_mux``/``ahp``
    toggles, so "No Mux + Launch in new window" still bypasses mux and an
    AHP-required worktree reaches ``new_window``'s explicit rejection
    instead of silently ignoring the toggle -- just adding
    ``new_window=True`` and running it directly instead of exiting the
    Picker through ``_decide``. (Composing with the separate "Bare resume"
    menu entry is not supported; that remains its own top-level verb.) Runs
    on a background thread (``screen._run_bg``) since opening the new window
    still shells out; reports success/failure through ``screen.debug``
    rather than raising through action dispatch.
    """
    from ... import __main__ as manager_main
    from .. import context
    from ...picker_app import LaunchRequest

    wt_id = (rec.get("raw") or {}).get("id")
    if not wt_id:
        screen.debug = "Launch in new window failed · no worktree id"
        return

    decision = screen._resume_decision(rec, no_mux=no_mux, ahp=ahp)
    opts = dict(decision.get("options") or {})
    project = context.project()

    def _work():
        request = LaunchRequest(
            project=project,
            worktree_id=str(wt_id),
            mode="resume",
            title=str(decision.get("title") or "") or None,
            no_mux=bool(opts.get("no_mux")),
            ahp=bool(opts.get("ahp")),
            new_window=True,
        )
        try:
            with _capture_stdout_for_this_thread() as buf:
                rc = manager_main._run_launch(request)
        except Exception as exc:  # pragma: no cover -- defensive; surfaced via screen.debug
            return False, str(exc)
        return rc == 0, buf.getvalue().strip() or None

    def _done(result):
        ok, detail = result
        screen.debug = (
            "Opened in a new window" if ok
            else f"Launch in new window failed · {detail or 'unknown error'}"
        )

    screen._run_bg("Launch in new window", _work, _done)
