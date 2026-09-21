"""Session inspection / lifecycle CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import sessions


def _core():
    from . import __main__ as core

    return core


def _json_error(*args, **kwargs):
    return _core()._json_error(*args, **kwargs)


def _json_output(*args, **kwargs):
    return _core()._json_output(*args, **kwargs)


def _read_hook_stdin(*args, **kwargs):
    return _core()._read_hook_stdin(*args, **kwargs)


def _run_session_lifecycle(*args, **kwargs):
    return _core()._run_session_lifecycle(*args, **kwargs)


def add_parsers(sub) -> None:
    sp = sub.add_parser(
        "session-lifecycle",
        help="Run the combined Copilot session-start lifecycle hook",
    )
    sp.add_argument(
        "--stdin",
        action="store_true",
        help="Read the Copilot sessionStart JSON payload from stdin",
    )
    sp.add_argument(
        "--timeout-seconds",
        type=float,
        default=10.0,
        help="Bound the combined lifecycle before the outer hook deadline",
    )

    sp = sub.add_parser(
        "session-binding",
        help="Resolve one live session's authoritative mux/process binding",
    )
    sp.add_argument("--session-id", required=True, help="Exact Copilot session id")
    sp.add_argument(
        "--json", action="store_true", help="Emit JSON (the default; accepted for consistency)"
    )

    sp = sub.add_parser(
        "session-recovery",
        help="Inspect one exact session projection for validated recovery guidance",
    )
    sp.add_argument(
        "--session-id",
        default=None,
        help="Exact Copilot session id (read from --stdin or "
        "COPILOT_AGENT_SESSION_ID when omitted)",
    )
    sp.add_argument("--cwd", default=None, help="Current session cwd for bound-here comparison")
    sp.add_argument(
        "--stdin",
        action="store_true",
        help="Read the Copilot sessionStart JSON payload from stdin",
    )
    sp.add_argument(
        "--emit-context", action="store_true", help="Emit bounded sessionStart additionalContext"
    )
    sp.add_argument(
        "--json", action="store_true", help="Emit JSON (the default; accepted for consistency)"
    )

    sp = sub.add_parser(
        "session-lineage",
        help="Show one exact session's bounded reciprocal lineage graph (JSON)",
    )
    sp.add_argument("--session-id", required=True, help="Exact Copilot session id")
    sp.add_argument(
        "--json", action="store_true", help="Emit JSON (the default; accepted for consistency)"
    )


def cmd_session_lifecycle(args: argparse.Namespace) -> int:
    """Run one combined session-start lifecycle pass."""
    payload = _read_hook_stdin() if getattr(args, "stdin", False) else {}
    timeout = max(0.1, float(getattr(args, "timeout_seconds", 10.0)))
    result = _run_session_lifecycle(payload or {}, deadline=time.time() + timeout)
    diagnostic = result.pop("_stderr", None)
    if diagnostic:
        print(str(diagnostic), file=sys.stderr, end="")
    print(json.dumps(result, separators=(",", ":")))
    return 0


def cmd_session_binding(args: argparse.Namespace) -> int:
    """Expose the authoritative session-to-mux binding as bounded JSON."""
    session_id = getattr(args, "session_id", None)
    binding = sessions.mux_binding_for_session(session_id) if session_id else None
    result = {
        "found": bool(binding),
        "session_id": session_id,
        "worktree_id": binding.get("worktree_id") if binding else None,
        "mux_session": binding.get("session_name") if binding else None,
        "pane_id": binding.get("pane_id") if binding else None,
        "pane_pid": binding.get("pane_pid") if binding else None,
        "pane_start_time": binding.get("pane_start_time") if binding else None,
        "copilot_pid": binding.get("copilot_pid") if binding else None,
        "copilot_start_time": (binding.get("copilot_start_time") if binding else None),
    }
    _json_output(result)
    return 0


def cmd_session_recovery(args: argparse.Namespace) -> int:
    """Inspect one exact session projection without changing authoritative state."""
    from . import session_projection

    session_id = getattr(args, "session_id", None)
    cwd = getattr(args, "cwd", None)
    if getattr(args, "stdin", False):
        payload = _read_hook_stdin()
        if payload:
            session_id = session_id or payload.get("sessionId")
            cwd = cwd or payload.get("cwd")
    session_id = session_id or os.environ.get("COPILOT_AGENT_SESSION_ID")
    if not isinstance(session_id, str) or not session_id:
        if getattr(args, "emit_context", False):
            print("{}")
            return 0
        return _json_error("an exact session id is required", exit_code=2)
    report = session_projection.recovery_report(session_id, cwd=cwd)
    if getattr(args, "emit_context", False):
        message = session_projection.render_recovery_context(report)
        print(
            json.dumps(
                {"additionalContext": message} if message else {},
                separators=(",", ":"),
            )
        )
        return 0
    _json_output(report)
    return 0


def cmd_session_lineage(args: argparse.Namespace) -> int:
    """Emit one exact session's reciprocal lineage without discovery scans."""
    from . import lineage_surfaces

    _json_output(lineage_surfaces.session_lineage(args.session_id))
    return 0
