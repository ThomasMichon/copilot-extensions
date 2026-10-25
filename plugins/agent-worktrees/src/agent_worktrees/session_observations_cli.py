"""Optional read-only single-session metadata CLI contracts."""

from __future__ import annotations

from . import output
from .session_attention import session_attention
from .session_observations import session_observations


def add_parsers(sub) -> None:
    """Register the metadata-only single-session provider."""
    parser = sub.add_parser(
        "session-observations",
        help="Read retained session tool/subagent metadata without transcript content (JSON)",
    )
    parser.add_argument("session_id", help="Copilot session ID (not a path)")
    parser.add_argument("--json", action="store_true", help="Emit JSON (the default)")
    parser = sub.add_parser(
        "session-attention",
        help="Read last-reported root attention without transcript content (JSON)",
    )
    parser.add_argument("session_id", help="Copilot session ID (not a path)")
    parser.add_argument("--json", action="store_true", help="Emit JSON (the default)")


def cmd_session_observations(args) -> int:
    """Emit evidence availability in JSON, including unavailable observations."""
    output._json_output(session_observations(args.session_id))
    return 0


def cmd_session_attention(args) -> int:
    """Emit last-reported state or explicit unknown evidence, without writes."""
    output._json_output(session_attention(args.session_id))
    return 0
