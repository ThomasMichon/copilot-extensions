"""Staged launch intent and cold-start eligibility."""

from __future__ import annotations

from .sessions import LiveVerdict
from . import launch_seed_state
from pathlib import Path


def seed_for_attempt(path: Path, record, args) -> launch_seed_state.LaunchSeed | None:
    explicit = getattr(args, "seed", None)
    expected_id = getattr(args, "seed_id", None)
    if explicit and expected_id:
        raise ValueError("Specify a prompt or a staged seed identity, not both")
    if explicit and not getattr(args, "dry_run", False):
        return launch_seed_state.stage(path, kind="resume", text=explicit)
    if explicit:
        return launch_seed_state.LaunchSeed("preview", "resume", explicit, 0)
    seed = launch_seed_state.peek(path)
    if getattr(record, "pending_seed", None):
        if getattr(args, "dry_run", False):
            return launch_seed_state.LaunchSeed("preview", "new", record.pending_seed, 0)
        seed = launch_seed_state.stage(path)
    if expected_id and (seed is None or seed.seed_id != expected_id):
        raise ValueError("Staged launch prompt is missing or superseded; refresh and retry")
    return seed


def plain_live_open(args, verdict: LiveVerdict | None) -> bool:
    return bool(
        not getattr(args, "seed", None) and not getattr(args, "seed_id", None)
        and verdict is not None and verdict.active
    )


def cold_start_error(verdict: LiveVerdict | None, kind: str = "resume") -> str | None:
    if verdict is None or not verdict.probes_ok or not verdict.mux_probe_ok:
        return (
            f"{kind.title()} prompt requires a confirmed stopped session; liveness "
            "could not be verified. Refresh and retry. The seed remains staged."
        )
    if verdict.active:
        return (
            f"{kind.title()} prompt is cold-start-only; this worktree already has a live "
            "session. Use Open instead. The seed remains staged."
        )
    return None
