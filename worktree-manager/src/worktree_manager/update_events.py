"""Fail-open update-run telemetry for ``worktree-manager update``.

The Manager normally owns the run: it creates it, exports the run identity to
the engine it drives (``agent-worktrees update --no-manager`` joins as a
participant), maps the engine's exit code through the shared table, and writes
the final status. Without the library every method is a no-op that keeps the
legacy return code.
"""

from __future__ import annotations

import contextlib

try:
    import update_telemetry as _ut
except ImportError:  # pragma: no cover - a slot built before the library existed
    _ut = None


class UpdateRun:
    """The Manager's view of one update run."""

    def __init__(self) -> None:
        self._run = _ut.Run.from_env_or_new("manager") if _ut is not None else None

    def self_update_phase(self):
        if self._run is None:
            return contextlib.nullcontext()
        return self._run.phase("manager_self_update", action="self_update")

    def record_self_update(self, action: str, cutover_failed: bool, detail: str | None) -> None:
        """A failed cutover fails the run; a skipped/failed self-update is advisory."""
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

    def finish(self, rc: int) -> int:
        """Close the run; ``rc`` is mapped through the shared exit-code table."""
        if self._run is None:
            return rc
        outcome, code = _ut.outcome_for_exit_code(rc)
        return self._run.finish(outcome, error={"code": code} if code else None)

    def abort(self, exc: BaseException) -> None:
        if self._run is not None:
            outcome, error = _ut.classify_exception(exc)
            self._run.finish(outcome, error=error)
