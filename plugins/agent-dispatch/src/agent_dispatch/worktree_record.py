"""Exact durable-record fallback for a worktree the roster has not published.

``agent-worktrees list`` serves the resident's roster projection, which
republishes a project only once per monitor sweep. A worktree created moments
ago (for example by a dispatch spawn whose bridge acquisition then failed) can
therefore be absent from ``list --fresh`` for minutes, and a retry that carries
it would hold as "unavailable" instead of resuming it. The owner's designated
exact-identity read before roster publication is
``agent-worktrees record --worktree-id <id> --json``; it never computes live
facts or grants launch authority.
"""

from __future__ import annotations

import json
import subprocess

from .procutil import no_window_kwargs


def read_exact_row(
    prefix: list[str], worktree_id: str, *, project: str | None,
    timeout: float | None, task_scoped: bool, env: dict[str, str] | None,
) -> dict | None:
    """The exact durable record as a roster-shaped row, or ``None`` if unproven."""
    cmd = [*prefix, *(["--project", project] if project else []),
           "record", "--worktree-id", worktree_id, "--json"]
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, launcher resolved locally
            cmd, check=False, capture_output=True, text=True, timeout=timeout,
            env=env, **no_window_kwargs(),
        )
        data = json.loads(result.stdout or "{}") if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None
    row = data.get("worktree") if isinstance(data, dict) and data.get("ok") is True else None
    if not isinstance(row, dict) or row.get("id") != worktree_id or not row.get("path"):
        return None
    # An owner too old to report dispatch provenance, or an unparseable
    # (opaque) attempt, cannot prove the worktree still belongs to this task,
    # so a task-scoped resolution stays unproven. An explicitly absent attempt
    # (``None``) is an untagged worktree and remains acceptable.
    if task_scoped and ("dispatch_attempt" not in row or row.get("dispatch_attempt_opaque")):
        return None
    return row
