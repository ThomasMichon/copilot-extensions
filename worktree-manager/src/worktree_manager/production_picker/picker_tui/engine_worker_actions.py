"""Worktree-row actions for the remote workers a worktree supervises.

venue-pivots-ux "Supervised workers, seen from the worktree row": a worktree's
Actions menu lists each supervised worker (``Worker: <venue>``), which jumps to
that venue's own pivot row and opens *its* action menu, so every venue action
(Open, Inspect, Watch, Release, ...) stays provider-contributed. This module
also hosts the provider-neutral internal verbs those venue rows use:
``open-venue-window`` (attach in a NEW terminal window, never displacing the
Picker) and ``open-bridge-ui`` (the live-session web view).
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

WORKER_VERB_PREFIX = "Worker: "


def venue_window_argv(argv, title, *, env=None, which=shutil.which, platform=None):
    """The argv that runs ``argv`` in a NEW terminal window/tab, or ``None``
    when no supported way is available: a ``tmux new-window`` when the Picker
    runs inside tmux (psmux on Windows), else a Windows Terminal ``new-tab``.
    Pure given ``env``/``which``/``platform`` so it is unit-testable."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    tmux = which("tmux") if env.get("TMUX") else None
    if tmux:
        joined = subprocess.list2cmdline(argv) if platform == "win32" else shlex.join(argv)
        return [tmux, "new-window", "-n", title, joined]
    wt = which("wt") if platform == "win32" else None
    if wt:
        return [wt, "-w", "0", "new-tab", "--title", title, *argv]
    return None


class PickerScreenWorkerActionsMixin:
    """Supervised-worker menu entries and the venue-window internal verbs."""

    def _worker_menu_verbs(self, rec):
        """``(labels, {label: worker})`` for ``rec``'s supervised workers, one
        ``Worker: <venue>`` verb each (duplicates dropped). Engine state only."""
        labels, by_label = [], {}
        for worker in self._worktree_supervised_workers(rec):
            label = WORKER_VERB_PREFIX + (worker.get("label") or worker.get("id") or worker["pivot"])
            if label not in by_label:
                labels.append(label)
                by_label[label] = worker
        return labels, by_label

    def _open_worker_row(self, worker):
        """Switch to the worker's venue pivot, focus its row, and open that
        row's own action menu. If the row isn't loaded in the current scope,
        land on the pivot and say so rather than guessing a row."""
        name = worker.get("pivot")
        idx = next((i for i, d in enumerate(self.pivots)
                    if getattr(d.get("pivot"), "name", None) == name), None)
        if idx is None:
            self.debug = f"worker: pivot {name} is not available"
            return
        self.htab, self.btn_idx, self.top = idx, 0, 0
        reg = self._reg_pivot()
        rows = self._task_rows()
        want = str(worker.get("id") or "")
        row = next((i for i, r in enumerate(rows)
                    if want and str(r.get(reg.id_field) or "") == want), None)
        if row is None:
            self.sel = self.default_sel()
            self.debug = f"worker: {want or name} is not loaded in this scope yet"
            self.refresh()
            return
        self.sel = ("T", row)
        self.refresh()
        self._open_task_menu()

    def _open_venue_window(self, ctx):
        """Internal verb ``open-venue-window``: attach the venue's session with
        its provider's ``copilot <id>`` verb in a NEW window (the Picker stays
        put). Carries the row's ``effort`` claim owner when present, so an
        effort-claimed venue isn't refused as busy. With no supported window
        mechanism, falls back to ``open-venue`` (exit the Picker and attach)."""
        provider, venue = ctx.get("provider"), ctx.get("id")
        if not provider or not venue:
            return False, "missing provider/venue identity for this row"
        binstub = shutil.which(str(provider))
        if not binstub:
            return False, f"'{provider}' is not on PATH"
        argv = [binstub, "copilot", str(venue)]
        effort = str(ctx.get("effort") or "").strip()
        if effort:
            argv += ["--effort", effort]
        launch = venue_window_argv(argv, str(venue))
        if launch is None:
            return self._open_venue(ctx)
        try:
            subprocess.Popen(launch, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return False, f"could not open a new window: {exc}"
        return True, f"opened {venue} in a new window"

    def _open_bridge_ui(self, _ctx):
        """Internal verb ``open-bridge-ui``: open agent-bridge's live-session
        web view (it signs the browser in with a one-time code)."""
        bridge = shutil.which("agent-bridge")
        if not bridge:
            return False, "'agent-bridge' is not on PATH"
        try:
            subprocess.Popen([bridge, "ui"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return False, f"could not start agent-bridge ui: {exc}"
        return True, "opening the live-session view"
