"""Runtime reap audit -- the first brick of the deliberate-reap-discipline
foundation (the reap-side mirror of ``agent_procutil``; vision behavior
*every-reap-is-deliberate-and-marked*).

Records every process/session reap at its call site -- the *target*, the
*reason*, whether it actually *killed*, the *caller stack*, and the *invoking
argv* -- to ``~/.agent-worktrees/logs/reap-audit.jsonl``. This makes the set of
reaps **enumerable at runtime**, complementing the static marker/checker: when a
reap misfires (e.g. an orphan/finished sweep racing a version cutover and
killing a live session), the log names exactly which caller reaped which target
and why -- the evidence a "why did my session die?" diagnosis needs.

Pure diagnostics: **logging only, no behavior change, never raises.** Stdlib-only
(so the dependency-free :mod:`agent_worktrees.procs` and
:mod:`agent_worktrees.sessions` can call it via a local import). Enabled by
default; set ``AGENT_WORKTREES_REAP_AUDIT=0`` to silence.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["record", "enabled", "log_path"]

_HOSTNAME = socket.gethostname()

# Frames from these modules are the reap plumbing itself -- trimmed from the
# recorded caller stack so the first "interesting" frame is the sweep/command
# that *decided* to reap.
_PLUMBING = ("reap_audit.py", "procs.py", "sessions.py")


def enabled() -> bool:
    """Audit is on unless explicitly disabled (``AGENT_WORKTREES_REAP_AUDIT=0``)."""
    return os.environ.get("AGENT_WORKTREES_REAP_AUDIT", "1") not in ("0", "false", "no")


def log_path() -> Path:
    """``~/.agent-worktrees/logs/reap-audit.jsonl`` (beside the activity log).

    Resolved stdlib-only (no ``config`` import) so the dependency-free callers
    stay dependency-free.
    """
    return Path(os.path.expanduser("~")) / ".agent-worktrees" / "logs" / "reap-audit.jsonl"


def _caller_frames(limit: int = 8) -> list[str]:
    """A trimmed caller chain: ``file:line:func`` for the frames *outside* the
    reap plumbing, nearest-caller first. Names the sweep/command that reaped."""
    frames: list[str] = []
    # Skip the current frame (this function) and its caller (``record``).
    for fr in reversed(traceback.extract_stack()[:-2]):
        name = os.path.basename(fr.filename)
        if name in _PLUMBING:
            continue
        frames.append(f"{name}:{fr.lineno}:{fr.name}")
        if len(frames) >= limit:
            break
    return frames


def record(
    kind: str,
    target: object,
    *,
    reason: str | None = None,
    killed: bool | None = None,
    **extra: object,
) -> None:
    """Append one reap-audit line. Best-effort; never raises; no behavior change.

    Args:
        kind: What is being reaped -- e.g. ``"mux-session"``, ``"pid"``,
            ``"cwd-proc"``.
        target: The thing reaped (session name, pid, ...).
        reason: The call site's own label for why (usually the function name).
        killed: Whether the underlying kill reported success, if known.
        **extra: Any extra context (name, root, worktree_id, ...); ``None``
            values are dropped.
    """
    if not enabled():
        return
    try:
        record: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": kind,
            "target": target,
            "reason": reason,
            "killed": killed,
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "host": _HOSTNAME,
            "argv": sys.argv,
            "caller": _caller_frames(),
        }
        # Correlate with the launch/session context when the environment carries it.
        for env_key, field in (
            ("WORKTREE_LAUNCH_ID", "launch_id"),
            ("AGENT_WORKTREES_BIND_WORKTREE_ID", "ctx_worktree_id"),
            ("AGENT_WORKTREES_BIND_SESSION_ID", "ctx_session_id"),
        ):
            val = os.environ.get(env_key)
            if val:
                record[field] = val
        for key, value in extra.items():
            if value is not None:
                record[key] = value
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=True, default=str)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        # A diagnostic log must never interfere with the reap it observes.
        pass
