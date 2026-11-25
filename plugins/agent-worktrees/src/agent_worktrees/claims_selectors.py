"""Explicit worktree selection and the claims CLI's legacy adapter."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys

from . import config as cfg
from .claims_transitive_cli import _is_safe_path_component

OWNER_OPERATIONS = frozenset({"add", "release", "settle", "annotate", "handoff"})
BATCH_OPERATIONS = frozenset({"reconcile-at-rest", "cleanup"})
WORKTREE_OPERATIONS = OWNER_OPERATIONS | BATCH_OPERATIONS | {"show", "transitive", "retained"}


def validate_scope(args: argparse.Namespace, operation: str) -> None:
    """Reject selectors that an operation cannot enforce before dispatch."""
    selected = list(getattr(args, "claim_worktree_ids", None) or [])
    old_owner = getattr(args, "release_worktree", None)
    if selected and operation not in WORKTREE_OPERATIONS:
        raise ValueError(f"claims {operation} does not accept --worktree-id")
    if old_owner and operation not in OWNER_OPERATIONS:
        raise ValueError(f"claims {operation} does not accept --worktree; use --worktree-id")
    if selected and old_owner:
        raise ValueError("use --worktree-id or deprecated --worktree, not both")
    if len(selected) > 1 and operation not in BATCH_OPERATIONS:
        raise ValueError(f"claims {operation} accepts only one --worktree-id")
    if getattr(args, "claim_owner_ref", None) and operation not in {
        "add",
        "settle",
        "binding",
        "ownership",
    }:
        raise ValueError(f"claims {operation} does not accept --owner-ref")
    if (
        (selected or old_owner)
        and getattr(args, "claim_owner_ref", None)
        and operation in OWNER_OPERATIONS
    ):
        raise ValueError("use --worktree-id or --owner-ref, not both")
    if getattr(args, "claim_refs", None) and operation != "cleanup":
        raise ValueError("--claim-ref is supported only by claims cleanup")
    for wid in [*selected, *([old_owner] if old_owner else [])]:
        if not isinstance(wid, str) or not _is_safe_path_component(wid):
            raise ValueError("--worktree-id must be a single safe worktree ID")


def worktree_ids(
    args: argparse.Namespace,
    legacy: Sequence[str] = (),
    *,
    operation: str,
    multiple: bool = False,
) -> list[str]:
    """Resolve named IDs while retaining a deprecated caller compatibility path."""
    selected = list(getattr(args, "claim_worktree_ids", None) or [])
    old_owner = getattr(args, "release_worktree", None)
    if selected and (legacy or old_owner):
        raise ValueError("do not combine --worktree-id with positional IDs or --worktree")
    if legacy and old_owner:
        raise ValueError("do not combine positional worktree IDs with --worktree")
    values = selected or ([old_owner] if old_owner else list(legacy))
    if not multiple and len(values) > 1:
        raise ValueError(f"claims {operation} accepts only one worktree ID")
    for wid in values:
        if not isinstance(wid, str) or not _is_safe_path_component(wid):
            raise ValueError("--worktree-id must be a single safe worktree ID")
    if legacy:
        print(
            f"Deprecated positional worktree selector; use claims {operation} --worktree-id ID.",
            file=sys.stderr,
        )
    elif old_owner:
        print("Deprecated --worktree selector; use --worktree-id.", file=sys.stderr)
    return values


def worktree_id(
    args: argparse.Namespace,
    legacy: Sequence[str] = (),
    *,
    operation: str,
) -> str | None:
    """Resolve one optional worktree ID."""
    values = worktree_ids(args, legacy, operation=operation)
    return values[0] if values else None


def existing_legacy_worktree(token: str) -> bool:
    """Recognize only an existing exact record as a deprecated bare selector."""
    if not _is_safe_path_component(token):
        return False
    try:
        return (cfg.tracking_dir() / f"{token}.yaml").is_file()
    except (OSError, RuntimeError, ValueError):
        return False


def cleanup_filters(args: argparse.Namespace) -> tuple[set[str], set[str], set[str]]:
    """Keep typed cleanup filters distinct from the legacy mixed selector set."""
    target = list(getattr(args, "target", None) or [])
    legacy = set(target[1:])
    worktrees = set(worktree_ids(args, operation="cleanup", multiple=True))
    refs = set(getattr(args, "claim_refs", None) or [])
    if legacy and (worktrees or refs):
        raise ValueError(
            "do not combine legacy cleanup selectors with --worktree-id or --claim-ref"
        )
    if legacy:
        print(
            "Deprecated positional cleanup selector; use --worktree-id or --claim-ref.",
            file=sys.stderr,
        )
    return legacy, worktrees, refs
