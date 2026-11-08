#!/usr/bin/env python3
"""Background executor for the picker's Cleanup / Sync progress sub-dialogs.

Runs the real per-worktree op on a daemon thread (sequentially, matching the
``working… N/M`` UX) so the Textual render loop never blocks. **Local** worktrees
run through the provider's attributable JSON CLI; **remote** worktrees run over
SSH per item against the project binstub's JSON CLI
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
import re
import subprocess
import threading

from agent_procutil import no_window_flags

from ... import engine_client
from ...engine_progress import failed_json_result
from .. import context
from .source_identity import local_key

# Per-item lifecycle states (mirror the progress sub-dialog glyphs).
PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"


def _ssh_json(argv, timeout=120, *, preserve_failure_detail=False):
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
    _cnw = no_window_flags()
    if os.name == "nt" and _cnw:
        kwargs["creationflags"] = _cnw
    proc = subprocess.run(argv, **kwargs)
    out = proc.stdout or ""
    i = out.find("{")
    if i < 0:
        if preserve_failure_detail:
            return failed_json_result(out, proc.stderr, reason="remote command returned no JSON")
        err = (proc.stderr or out or "").strip().splitlines()
        return {"ok": False, "reason": err[-1] if err else f"exit {proc.returncode}"}
    try:
        obj, _ = json.JSONDecoder().raw_decode(out[i:])
        if (preserve_failure_detail and isinstance(obj, dict) and proc.stderr
                and (proc.returncode or obj.get("success") is False or obj.get("error"))):
            obj["detail"] = "\n".join(
                str(part) for part in (obj.get("detail"), proc.stderr.strip()) if part
            )
        return obj
    except ValueError:
        if preserve_failure_detail:
            return failed_json_result(out, proc.stderr, reason="unparseable remote result")
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
    if op == "reclaim":
        # reclaim_one's ``ok`` is authoritative: True when every targeted bound
        # process was terminated (or there was nothing bound to reclaim).
        return bool(res.get("ok"))
    if op == "finalize":
        # validate_and_finalize / `finalize --json`: ``success`` is authoritative
        # A refusal is expected on unsettled work, not an invitation to force.
        return res.get("success") is True
    # sync: a no-op that was already current is success; a real skip is not.
    if "ok" in res:
        return res["ok"] is True
    return bool(res.get("updated")) or res.get("reason") == "up-to-date"


def sync_diagnostics(result):
    """Keep the cause and any earlier paired progress visible in the dialog."""
    if not isinstance(result, dict):
        return []

    def compact(value, limit=160):
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else "..." + text[-(limit - 3):]

    lines = []
    reason = compact(result.get("reason") or result.get("error"))
    detail = compact(result.get("detail"))
    if reason not in ("", "updated", "up-to-date"):
        lines.append(compact(f"{reason}: {detail}" if detail else reason))
    elif detail:
        lines.append(detail)
    members = result.get("pair_results")
    if isinstance(members, list):
        progress = []
        for member in members[:2]:
            if not isinstance(member, dict):
                continue
            label = member.get("role") or str(member.get("worktree_id") or "member").rsplit("-", 1)[-1]
            outcome = member.get("reason") or ("updated" if member.get("updated") else "unchanged")
            progress.append(f"{compact(label, 12)}: {compact(outcome, 24)}")
        if progress:
            lines.append("; ".join(progress))
    return lines


def finalize_diagnostics(result):
    """Keep the first actual refusal, not the tail of a long claim ledger."""
    if not isinstance(result, dict):
        return []
    reason = result.get("reason") or result.get("error")
    if not reason:
        lines = [
            re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            for line in str(result.get("detail") or "").splitlines() if line.strip()
        ]
        reason = next(
            (line for line in lines if line.startswith(("✗", "[ERROR]", "ERROR:"))),
            lines[0] if lines else "",
        )
    text = " ".join(str(reason or "").split())
    return [text if len(text) <= 160 else text[:157] + "..."] if text else []


def build_tasks(op, items, src, *, project=None, include_unused=False,
                include_conversations=False,
                local: tuple[str | None, str | None] | None = None):
    """Build ``(key, callable)`` tasks for *items* under data source *src*.

    *items* are engine record dicts (collision-safe ``selection_id`` +
    ``raw.id`` + ``machine`` / ``env``). Local items
    (machine/env == the selected local source key) call the attributable provider JSON CLI;
    remote items call the same project CLI over SSH via
    ``data_ssh.remote_op_argv``. A target that resolves to neither (unknown /
    *not-ready, or no remote argv builder on the source) yields a failed task.
    UI callers pass their setup-published ``local`` tuple; only worker/legacy
    callers may resolve the source when that optional argument is absent.
    """
    if project is None:
        project = context.project()
    elif not project.strip():
        raise ValueError("project must not be empty")
    if local is None:
        local = local_key(src)
    tasks = []
    for w in items:
        wt_id = (w.get("raw") or {}).get("id")
        key = w.get("selection_id") or w.get("id4") or wt_id
        m, e = w.get("machine"), w.get("env")
        source_id = w.get("source_id")
        is_local = (
            w.get("source_kind", "machine-ssh") == "machine-ssh"
            and (m, e) == local
        )
        refusal = _finalize_blocker(w) if op == "finalize" else None
        tasks.append((key, _make_task(
            op, wt_id, m, e, is_local, project=project, source_id=source_id,
            include_unused=include_unused,
            include_conversations=include_conversations,
            refusal=refusal,
        )))
    return tasks


def _finalize_blocker(row):
    if row.get("managed_child"):
        return "controller-owned workspace: return to its controller"
    if row.get("mux_observation_unknown"):
        return "mux observation unavailable: refresh qualified status before finalizing"
    if any(row.get(field) for field in (
        "mux_live", "session_bare_orphan", "session_lock_live",
        "session_bound_live", "session_bridge_live", "session_lock_stale",
        "session_ahp_live", "execution_leg_live",
    )):
        return "live session or lock residue: resolve its lifecycle before finalizing"
    return None


def _make_task(op, wt_id, machine, env, is_local, *, project, source_id=None,
               include_unused=False, include_conversations=False, refusal=None):
    def _run():
        if refusal:
            return {"success": False, "reason": refusal}
        if not wt_id:
            return {"ok": False, "reason": "no worktree id"}
        if is_local:
            if op == "cleanup":
                args = ["cleanup", "--worktree-id", wt_id, "--clean", "--json"]
                if include_unused:
                    args.append("--include-unused")
                if include_conversations:
                    args.append("--include-conversations")
            elif op == "restart":
                args = ["restart", wt_id, "--json"]
            elif op == "reclaim":
                args = [
                    "reclaim", "--worktree-id", wt_id,
                    "--bare-only", "--yes", "--json",
                ]
            elif op == "finalize":
                args = ["finalize", wt_id, "--json"]
            else:
                args = ["sync", "--paired", "--worktree-id", wt_id, "--json"]
            return engine_client.run_json(
                project, args, timeout=120, allow_nonzero=True,
                **({"preserve_failure_detail": True} if op == "finalize" else {}),
            )
        from . import data_ssh
        argv = data_ssh.remote_op_argv(
            machine, env, op, wt_id,
            source_id=source_id,
            include_unused=include_unused,
            include_conversations=include_conversations,
        )
        if argv is None:
            return {
                "ok": False,
                "reason": f"source does not support {op}: {source_id or f'{machine} {env}'}",
            }
        return _ssh_json(argv, preserve_failure_detail=True) if op == "finalize" else _ssh_json(argv)
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
