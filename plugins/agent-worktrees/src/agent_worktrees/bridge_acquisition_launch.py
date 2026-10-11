"""Bridge-owned CLI launch intent, validated under the worktree lifecycle lock."""

from __future__ import annotations

import argparse

from . import sessions
from .session_liveness import session_liveness


def add_arguments(parser: argparse._ActionsContainer) -> None:
    parser.add_argument(
        "--bridge-acquisition", metavar="NATIVE-ID|fresh",
        help="Bridge-owned unseeded launch; exact native resume or explicit fresh. "
        "Refuses a live mux and does not consume pending worktree seeds.",
    )


def refusal(args: argparse.Namespace, record: object, worktree_id: str) -> str | None:
    intent = getattr(args, "bridge_acquisition", None)
    if not intent:
        return None
    if getattr(args, "seed", None) or getattr(args, "copilot_args", None):
        return "bridge acquisition cannot carry a seed or unchecked Copilot arguments"
    if sessions.has_mux_session(worktree_id):
        return "a live mux holds the bridge acquisition target"
    # Query the same authoritative reduction ordinary resume uses, without its
    # filesystem fallback; absence must not be filled by a random transcript.
    head = getattr(record, "resolved_head_session", None)
    if intent != "fresh" and intent != head:
        return "bridge acquisition native identity does not match the worktree head"
    if head and session_liveness(head) != "dead":
        return "cannot prove the prior native conversation has stopped"
    return None
