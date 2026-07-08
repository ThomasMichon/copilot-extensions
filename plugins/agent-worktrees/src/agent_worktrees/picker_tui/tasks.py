"""Runtime for cross-plugin *registered* pivots in the Textual picker.

A registered pivot (see :mod:`picker_tui.pivots`) declares a ``list`` command
that prints a JSON array of entries, and an ``actions`` set of argv templates.
This module runs those commands -- always as a subprocess against the
contributing plugin's CLI on ``PATH``, never a cross-venv Python import -- so
the picker stays decoupled from the plugin's runtime.

:class:`RegisteredPivotRuntime` keeps the picker responsive: the ``list``
command runs on a daemon thread and the result is cached per machine, so the
render loop only ever reads a snapshot. Everything degrades gracefully -- a
missing CLI, a non-zero exit, or malformed JSON becomes an ``error`` state the
pivot surfaces, never an exception that breaks the picker.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections.abc import Mapping, Sequence

from .pivots import RegisteredPivot, format_template

#: Hard cap on how long a pivot's ``list``/action command may run.
LIST_TIMEOUT = 20.0
ACTION_TIMEOUT = 30.0


def _resolve_argv(template: Sequence[str], ctx: Mapping[str, object]) -> list[str]:
    """Substitute placeholders and resolve argv[0] via ``PATH`` (so a bare
    ``agent-dispatch`` runs regardless of the picker's own venv)."""
    argv = format_template(template, ctx)
    if argv:
        resolved = shutil.which(argv[0])
        if resolved:
            argv = [resolved, *argv[1:]]
    return argv


class RegisteredPivotRuntime:
    """Background loader + action runner for one registered pivot.

    Thread-safe: the render loop calls :meth:`ensure` / :meth:`get` on every
    frame; a single daemon thread per machine fetches the ``list`` output.
    """

    def __init__(self, pivot: RegisteredPivot):
        self.pivot = pivot
        self._lock = threading.Lock()
        # machine -> (state, rows, error). state: loading|ready|error.
        self._cache: dict[object, tuple[str, list, str]] = {}
        self._inflight: set[object] = set()

    # -- listing -------------------------------------------------------------

    def ensure(self, machine: object) -> None:
        """Kick off a background ``list`` fetch for ``machine`` if not already
        cached or in flight. Cheap + idempotent -- safe to call every frame."""
        with self._lock:
            if machine in self._cache or machine in self._inflight:
                return
            self._inflight.add(machine)
        threading.Thread(target=self._run_list, args=(machine,), daemon=True).start()

    def get(self, machine: object) -> tuple[str, list, str]:
        """The cached ``(state, rows, error)`` for ``machine`` (``idle`` before
        :meth:`ensure` has been called, ``loading`` while a fetch is running)."""
        with self._lock:
            if machine in self._cache:
                return self._cache[machine]
            if machine in self._inflight:
                return ("loading", [], "")
        return ("idle", [], "")

    def invalidate(self, machine: object = None) -> None:
        """Drop cached results so the next :meth:`ensure` refetches. ``None``
        clears every machine (used after an action mutates the queue)."""
        with self._lock:
            if machine is None:
                self._cache.clear()
            else:
                self._cache.pop(machine, None)

    def _run_list(self, machine: object) -> None:
        ctx = {"machine": "" if machine is None else str(machine)}
        result = self._exec_list(ctx)
        with self._lock:
            self._cache[machine] = result
            self._inflight.discard(machine)

    def _exec_list(self, ctx: Mapping[str, object]) -> tuple[str, list, str]:
        argv = _resolve_argv(self.pivot.list_cmd, ctx)
        if not argv:
            return ("error", [], "empty list command")
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=LIST_TIMEOUT, check=False
            )
        except FileNotFoundError:
            return ("error", [], f"{argv[0]} not found on PATH")
        except (OSError, subprocess.SubprocessError) as exc:
            return ("error", [], str(exc)[:200])
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            msg = detail[-1] if detail else f"exit {proc.returncode}"
            return ("error", [], msg[:200])
        try:
            data = json.loads(proc.stdout or "[]")
        except ValueError:
            return ("error", [], "list command did not print JSON")
        rows = data if isinstance(data, list) else []
        rows = [r for r in rows if isinstance(r, dict)]
        return ("ready", rows, "")

    # -- actions -------------------------------------------------------------

    def run_action(self, action, ctx: Mapping[str, object]) -> tuple[bool, str]:
        """Run one action's argv template against ``ctx``. Returns
        ``(ok, message)`` -- never raises, so the caller can surface the result
        in the status line."""
        argv = _resolve_argv(action.run, ctx)
        if not argv:
            return (False, "empty action command")
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=ACTION_TIMEOUT, check=False
            )
        except FileNotFoundError:
            return (False, f"{argv[0]} not found on PATH")
        except (OSError, subprocess.SubprocessError) as exc:
            return (False, str(exc)[:200])
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            return (False, (detail[-1] if detail else f"exit {proc.returncode}")[:200])
        return (True, (proc.stdout or "").strip()[:200])
