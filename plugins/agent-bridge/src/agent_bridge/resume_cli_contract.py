"""Exact-target resume identity and executing-CLI support declarations."""

from __future__ import annotations

import argparse
import re
from typing import Any


def exact_worktree_id(value: str) -> str:
    """Accept one owner-record ID, never a path, alias or singleton key."""
    if not isinstance(value, str) or re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", value,
    ) is None or ".." in value:
        raise argparse.ArgumentTypeError("expected one exact owner-record worktree ID")
    return value


def resume_target(args: argparse.Namespace) -> tuple[str, bool]:
    """Validate exclusive target selection before client discovery or I/O."""
    worktree = getattr(args, "worktree_id", None)
    target = getattr(args, "session_id", None)
    if worktree is not None:
        if target is not None:
            raise argparse.ArgumentTypeError("target and --worktree-id are mutually exclusive")
        return exact_worktree_id(worktree), True
    if not isinstance(target, str) or not target.strip():
        raise argparse.ArgumentTypeError("target or --worktree-id is required")
    return target, False


def confirmed_resume(result: Any, worktree_id: str, *, cli: bool) -> bool:
    """Check returned identities without inventing native or readiness facts."""
    if not isinstance(result, dict):
        return False
    fields = ("session_id", "acp_session_id", "durable_session_id")
    if any(not isinstance(result.get(key), str) or not result[key].strip() for key in fields):
        return False
    native = result["acp_session_id"]
    return (
        result.get("worktree_id") == worktree_id
        and result.get("mode") == ("cli" if cli else "acp")
        and result["durable_session_id"] == native
        and (not cli or result["session_id"] == native)
    )


def cli_contracts() -> dict[str, Any]:
    """Describe this executing CLI, not an installed payload or live daemon."""
    return {
        "schema": "agent-bridge.cli-contracts",
        "version": 1,
        "contracts": {
            "agent-bridge.resume": {
                "version": 1,
                "capabilities": ["exact-worktree-target"],
            },
        },
    }
