#!/usr/bin/env python3
"""Background executor for the picker's Cleanup / Sync progress sub-dialogs.

Runs the real per-worktree op on a daemon thread (sequentially, matching the
``working… N/M`` UX) so the Textual render loop never blocks. **Local** worktrees
run in-process via the ``__main__`` pure helpers (``reap_one`` / ``sync_one``) or
the ``sessions`` primitive (``restart_worktree_copilot``); **remote** worktrees
run over SSH per item against the project binstub's JSON CLI
(``cleanup --worktree-id`` / ``sync --worktree-id`` / ``restart <id>`` /
``finalize --worktree-id``). The engine polls each item's state from its render
tick.

The executor is the *real* counterpart to the engine's mock progress walker
(``_advance_progress``). Real ops are the **default**, so Maintenance
Sync/Cleanup actually mutate worktrees; the mock simulation runs only in the
picker's explicit **mock mode** (``picker mock`` / ``AGENT_WORKTREES_PICKER_MOCK``
-- the dev sandbox), in which case the engine runs the walker instead of this
executor.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading

# Per-item lifecycle states (mirror the progress sub-dialog glyphs).
PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"


def _ssh_json(argv, timeout=120):
    """Run a remote op over SSH and parse its single JSON result object."""
    kwargs = dict(
        capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        # Detach from the console stdin so the remote ``ssh`` op can't read the
        # operator's keystrokes out from under the TUI (see data_ssh._run).
        stdin=subprocess.DEVNULL,
    )
    # On Windows, also keep the child off our console so a failing ssh can't
    # clear the console's VT-input mode and break arrow keys (see data_ssh).
    _cnw = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name == "nt" and _cnw:
        kwargs["creationflags"] = _cnw
    proc = subprocess.run(argv, **kwargs)
    out = proc.stdout or ""
    i = out.find("{")
    if i < 0:
        err = (proc.stderr or out or "").strip().splitlines()
        return {"ok": False, "reason": err[-1] if err else f"exit {proc.returncode}"}
    try:
        obj, _ = json.JSONDecoder().raw_decode(out[i:])
        return obj
    except ValueError:
        return {"ok": False, "reason": "unparseable remote result"}


def _result_ok(op, res):
    """Map a per-item result dict onto DONE/FAILED for the progress glyph."""
    if op == "cleanup":
        return bool(res.get("removed")) and bool(res.get("ok", True))
    if op == "profiles":
        # Profiles Apply: the apply_column result's ``ok`` is authoritative.
        return bool(res.get("ok"))
    if op == "restart":
        # restart_worktree_copilot's ``ok`` is authoritative: True whether it
        # stopped a live session (method graceful/hard) or found nothing running
        # (method none) -- either way the worktree is now free to re-Open.
        return bool(res.get("ok"))
    if op == "finalize":
        # validate_and_finalize / `finalize --json`: ``success`` is authoritative
        # (False when content is unpushed, but the picker only offers Finalize on
        # no-commit conversation/unused worktrees, so it succeeds).
        return bool(res.get("success"))
    # sync: a no-op that was already current is success; a real skip is not.
    return bool(res.get("updated")) or res.get("reason") == "up-to-date"


def build_tasks(op, items, src, *, include_unused=False,
                include_conversations=False):
    """Build ``(key, callable)`` tasks for *items* under data source *src*.

    *items* are engine record dicts (``id4`` + ``raw.id`` + ``machine`` /
    ``env``). Local items (machine/env == ``src.LOCAL``) call the in-process
    ``__main__`` helper; remote items call the SSH CLI via
    ``data_ssh.remote_op_argv``. A target that resolves to neither (unknown /
    *not-ready, or no remote argv builder on the source) yields a failed task.
    """
    local = getattr(src, "LOCAL", None)
    tasks = []
    for w in items:
        wt_id = (w.get("raw") or {}).get("id")
        key = w.get("id4") or wt_id
        m, e = w.get("machine"), w.get("env")
        is_local = (m, e) == local
        tasks.append((key, _make_task(
            op, wt_id, m, e, is_local,
            include_unused=include_unused,
            include_conversations=include_conversations,
        )))
    return tasks


def _make_task(op, wt_id, machine, env, is_local, *, include_unused,
               include_conversations):
    def _run():
        if not wt_id:
            return {"ok": False, "reason": "no worktree id"}
        if is_local:
            from .. import __main__ as cli
            if op == "cleanup":
                return cli.reap_one(
                    wt_id, include_unused=include_unused,
                    include_conversations=include_conversations,
                )
            if op == "restart":
                from .. import sessions
                return sessions.restart_worktree_copilot(wt_id)
            if op == "finalize":
                return cli.finalize_one(wt_id)
            return cli.sync_one(wt_id)
        from . import data_ssh
        argv = data_ssh.remote_op_argv(
            machine, env, op, wt_id,
            include_unused=include_unused,
            include_conversations=include_conversations,
        )
        if argv is None:
            return {"ok": False, "reason": f"no remote route to {machine} {env}"}
        return _ssh_json(argv)
    return _run


class MaintenanceExecutor:
    """Runs maintenance tasks sequentially on a daemon thread.

    The engine polls :meth:`state` / :meth:`result` / :meth:`is_done` /
    :meth:`counts` each render tick; a slow or failing remote never blocks or
    crashes the UI.
    """

    def __init__(self, op, tasks):
        self.op = op
        self._tasks = list(tasks)
        self._lock = threading.Lock()
        self._state = {k: PENDING for k, _ in self._tasks}
        self._result = {}
        self._done = False

    def start(self):
        threading.Thread(target=self._run, name="maint-exec", daemon=True).start()

    def _run(self):
        for key, fn in self._tasks:
            with self._lock:
                self._state[key] = RUNNING
            try:
                res = fn()
                st = DONE if _result_ok(self.op, res) else FAILED
            except Exception as exc:  # any failure -> failed item
                res, st = {"ok": False, "reason": str(exc) or type(exc).__name__}, FAILED
            with self._lock:
                self._result[key] = res
                self._state[key] = st
        with self._lock:
            self._done = True

    def state(self, key):
        with self._lock:
            return self._state.get(key, PENDING)

    def result(self, key):
        with self._lock:
            return self._result.get(key)

    def is_done(self):
        with self._lock:
            return self._done

    def counts(self):
        """(done, failed, remaining) across all tasks."""
        with self._lock:
            vals = list(self._state.values())
        done = sum(1 for v in vals if v == DONE)
        failed = sum(1 for v in vals if v == FAILED)
        return done, failed, len(vals) - done - failed
