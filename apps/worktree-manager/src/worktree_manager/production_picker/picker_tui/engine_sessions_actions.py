#!/usr/bin/env python3
"""PickerScreen mixin: the "Sessions" sub-menu overlay (#3307 Phase 7).

Split out of ``engine_worktree_actions.py`` (module-size cap): browsing a
worktree's full session HISTORY is a cohesive, separable concern from that
file's worktree lifecycle-action plumbing (Open/Resume/Stop/Reclaim/...),
even though :meth:`PickerScreenSessionsActionsMixin._sessionsview_worker`
reuses that mixin's ``_load_worktree_sessions`` fetch -- both mixins compose
onto the same ``PickerScreen``, so ``self`` sees either's methods.
"""
from __future__ import annotations

import threading

from .engine_live_screens import SessionsViewScreen

class PickerScreenSessionsActionsMixin:
    def _open_sessions_menu(self, rec):
        """Open the "Sessions" sub-menu overlay (#3307 Phase 7): every session
        EVER registered against ``rec``'s worktree (id, started/ended, turn
        count, head marker) -- a dedicated, read-only history browse, distinct
        from ``MsgViewScreen``'s abbreviated per-session list (a copy-an-id aid
        alongside the CURRENT session's recent-messages tail).

        Mirrors ``_open_msgview``'s native-``ModalScreen`` shape exactly: this
        builds the engine-owned ``self.sessionsview`` dict, starts the daemon
        loader thread (which populates it under ``_sessionsview_lock``, reusing
        the SAME ``_load_worktree_sessions`` fetch ``_open_msgview`` already
        uses), then pushes the ``SessionsViewScreen`` that renders it.
        """
        self.sessionsview = {
            "rec": rec, "loading": True, "sessions": [], "error": None,
            "scroll": 0,
        }
        threading.Thread(
            target=self._sessionsview_worker, args=(rec,),
            name="sessionsview-load", daemon=True,
        ).start()
        self.app.push_screen(SessionsViewScreen(self))
    def _sessionsview_worker(self, rec):
        """Daemon-thread body: fetch the worktree's full session registry."""
        wt_id = (rec.get("raw") or {}).get("id")
        m, e = rec.get("machine"), rec.get("env")
        try:
            sessions_list = self._load_worktree_sessions(rec, wt_id, m, e) if wt_id else []
            error = None if wt_id else "no worktree id"
        except Exception as exc:  # never let the loader thread crash the UI
            sessions_list, error = [], (str(exc) or type(exc).__name__)
        with self._sessionsview_lock:
            # A late result for a viewer the operator already closed/reopened
            # is dropped (identity check on the live overlay's rec), mirroring
            # ``_msgview_worker``'s own guard.
            sv = self.sessionsview
            if sv is None or sv.get("rec") is not rec:
                return
            sv["loading"] = False
            sv["sessions"] = sessions_list
            sv["error"] = error
    def _key_sessionsview(self, key):
        """Keys for the Sessions sub-menu overlay: scroll + close."""
        sv = self.sessionsview
        if key in ("escape", "q", "tab", "enter"):
            self.sessionsview = None
            return
        if key == "down":
            sv["scroll"] = sv.get("scroll", 0) + 1
        elif key == "up":
            sv["scroll"] = max(0, sv.get("scroll", 0) - 1)
