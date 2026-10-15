"""Fail-open update-run telemetry for ``worktree-manager update``.

The Manager normally owns the run: it creates it, exports the run identity to
the engine it drives (``agent-worktrees update --no-manager`` joins as a
participant), maps the engine's exit code through the shared table, and writes
the final status. Without the library every method is a no-op that keeps the
legacy return code.
"""

from __future__ import annotations

import contextlib
import sys

try:
    from update_telemetry.human import HumanOperation, current_operation
except ImportError:  # older runtimes retain their original output
    HumanOperation = None

try:
    import update_telemetry as _ut
except ImportError:  # pragma: no cover - a slot built before the library existed
    _ut = None


class UpdateRun:
    """The Manager's view of one update run."""

    def __init__(self) -> None:
        self._human = HumanOperation("update/manager") if HumanOperation else None
        self._self_update_human = None
        if self._human is not None:
            self._human.__enter__()
        try:
            self._run = _ut.Run.from_env_or_new("manager") if _ut is not None else None
        except BaseException as exc:
            if self._human is not None:
                self._human.__exit__(type(exc), exc, exc.__traceback__)
            raise

    @contextlib.contextmanager
    def self_update_phase(self):
        telemetry = (
            self._run.phase("manager_self_update", action="self_update")
            if self._run is not None else contextlib.nullcontext()
        )
        scope = HumanOperation("manager_self_update") if HumanOperation else contextlib.nullcontext()
        with scope as human, telemetry:
            self._self_update_human = human
            try:
                yield
            finally:
                self._self_update_human = None

    def say(self, message: str, *, state: str | None = None) -> None:
        if self._human is not None:
            current_operation().progress(message, state=state)
        else:
            print(message)

    def record_self_update(self, action: str, cutover_failed: bool, detail: str | None) -> None:
        """A failed cutover fails the run; a skipped/failed self-update is advisory."""
        state = (
            "failed" if cutover_failed else "done" if action == "updated"
            else "current" if action == "already-current"
            else "warning" if action == "error" else "skipped"
        )
        if self._self_update_human is not None:
            self._self_update_human.complete(state, detail or "")
        if self._run is None:
            return
        required, error = True, None
        if cutover_failed:
            outcome, error = _ut.COMMAND_FAILED, {"code": "cutover_failed", "message": detail or ""}
        elif action in ("updated", "already-current"):
            outcome = _ut.VERIFIED_CURRENT
        else:
            required = False
            outcome = _ut.COMMAND_FAILED if action == "error" else _ut.SKIPPED
        self._run.target("manager_self_update", "manager", outcome, required=required,
                         changed=action == "updated", detail=detail, error=error)

    def record_engine_error(self, message: str, *, missing: bool,
                            cause: BaseException | None = None) -> None:
        """The engine could not be run at all (absent, or failed to start).

        A launch failure is classified by its underlying ``OSError`` cause, so
        a sharing violation stays ``blocked_by_lock`` and permission or I/O
        errors keep their own codes.
        """
        if self._run is None:
            return
        if missing:
            outcome, error = _ut.CONFIGURATION_LIMITATION, {"code": "engine_missing"}
        elif isinstance(cause, OSError):
            outcome, error = _ut.classify_exception(cause, op="engine_launch")
        else:
            outcome, error = _ut.COMMAND_FAILED, {"code": "engine_error"}
        error["message"] = message
        self._run.target("runtime_reconcile", "engine", outcome, detail=message, error=error)

    def finish(self, rc: int) -> int:
        """Close the run; ``rc`` is mapped through the shared exit-code table.

        A generic failure (1) defers to a more specific recorded outcome.
        """
        try:
            if self._run is not None:
                outcome, code = _ut.outcome_for_exit_code(rc)
                if rc == 1 and _ut.is_failure(self._run.outcome):
                    outcome = None
                rc = self._run.finish(outcome, error={"code": code} if code else None)
            if self._human is not None:
                self._human.complete("done" if rc == 0 else "failed", f"exit {rc}")
            return rc
        except BaseException as exc:
            if self._human is not None:
                self._human.complete("failed", f"{type(exc).__name__}: {exc}")
            raise
        finally:
            if self._human is not None:
                self._human.__exit__(*sys.exc_info())

    def abort(self, exc: BaseException) -> None:
        try:
            if self._run is not None:
                outcome, error = _ut.classify_exception(exc)
                self._run.finish(outcome, error=error)
        finally:
            if self._human is not None:
                self._human.__exit__(type(exc), exc, exc.__traceback__)
