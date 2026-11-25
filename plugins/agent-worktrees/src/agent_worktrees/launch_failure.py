"""Prompt-free failure observations; never acceptance or permission to replay."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .launch_execution import Execution


def observe(
    record_path: Path, reason: str, *, execution: Execution | None = None,
    error: BaseException | None = None, exit_code: int | None = None,
) -> None:
    """Keep a fixed caller-supplied reason, not exception text, argv or prompts."""
    from . import activity

    subject = execution.subject if execution else {}
    activity.log_event(
        "launch_seed_failed", worktree_id=record_path.stem,
        reason=reason, exit_code=exit_code,
        attempt_id=subject.get("attempt_id"), resolution_id=subject.get("resolution_id"),
        operation_id=subject.get("operation_id"), parent_id=subject.get("parent_id"),
        error_type=type(error).__name__ if error is not None else None,
        errno=error.errno if isinstance(error, OSError) else None,
        winerror=getattr(error, "winerror", None) if isinstance(error, OSError) else None,
    )


def refuse(
    record_path: Path, reason: str, message: str, *,
    execution: Execution | None = None, error: BaseException | None = None,
) -> int:
    observe(record_path, reason, execution=execution, error=error, exit_code=3)
    print(message, file=sys.stderr)
    return 3
