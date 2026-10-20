"""Bridge-owned CLI launch intent, validated under the worktree lifecycle lock."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path

from . import finalize, launch_admission, output, sessions
from .session_liveness import session_liveness


def add_arguments(
    parser: argparse.ArgumentParser, intent_group: argparse._ActionsContainer,
) -> None:
    intent_group.add_argument(
        "--bridge-acquisition", metavar="NATIVE-ID|fresh",
        help="Bridge-owned unseeded launch; exact native resume or explicit fresh. "
        "Refuses a live mux and does not consume pending worktree seeds.",
    )
    parser.add_argument(
        "--require-native-admission", action="store_true",
        help="Require the exact-head, owner-fenced cold-resume contract",
    )


@contextmanager
def launch_fence(repo, record_path: Path):
    """Order lifecycle and per-worktree native admission around real mux start."""
    lifecycle = finalize.FinalizeLock(
        Path(repo.worktree_root) / ".finalize.lock", timeout=300, stale_after=3600,
    )
    with output.stdout_to_stderr():
        lifecycle.acquire()
    try:
        with launch_admission.guard(record_path):
            yield
    finally:
        lifecycle.release()


def refusal(args: argparse.Namespace, record: object, worktree_id: str) -> str | None:
    intent = getattr(args, "bridge_acquisition", None)
    if getattr(args, "require_native_admission", False) and (not intent or intent == "fresh"):
        return "native admission requires an exact existing native conversation"
    if not intent:
        return None
    if getattr(record, "status", None) == "finalizing":
        return "the worktree is finalizing and cannot start a native conversation"
    if getattr(record, "pending_handoffs", ()):
        return "the native worktree head has a pending handoff"
    if getattr(record, "execution_leg_opaque", False) or getattr(record, "session_backend_opaque", False):
        return "native execution authority is opaque"
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
