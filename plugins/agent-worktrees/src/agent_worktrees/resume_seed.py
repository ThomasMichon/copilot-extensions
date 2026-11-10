"""Staged launch intent and cold-start eligibility."""

from __future__ import annotations

import os

from .sessions import LiveVerdict
from . import launch_admission, launch_seed_state
from pathlib import Path


RETAINED_HANDOFF_WARNING = (
    "Launch prompt retained; opening without submitting it again. "
    "Opening does not confirm any previous handoff."
)


def preserve_unconfirmed_seed(args, seed: launch_seed_state.LaunchSeed | None) -> bool:
    return bool(
        seed is not None
        and (
            getattr(args, "preserve_seed_id", None) is not None
            or seed.handoff_id and os.environ.get("AGENT_WORKTREES_RETAINED_SEED_PLANS") == "1"
        )
        and not getattr(args, "seed", None)
        and not getattr(args, "seed_id", None)
        and not getattr(args, "stage_launch_seed", False)
    )


def preserved_seed_selection_error(args) -> str | None:
    selected = getattr(args, "preserve_seed_id", None)
    if selected is None:
        return None
    if (
        not isinstance(selected, str) or not selected
        or not getattr(args, "worktree_id", None)
        or getattr(args, "seed", None) is not None
        or getattr(args, "seed_id", None) is not None
        or any(getattr(args, name, None) for name in (
            "new_worktree", "base", "machine", "environment", "restore",
            "stage_launch_seed", "native_resume_session", "launch_request_b64", "launch_request_status",
        ))
    ):
        return "--preserve-seed-id requires an existing local worktree without prompt delivery."
    return None


def seed_for_attempt(
    path: Path, record, args, *, verdict: LiveVerdict | None = None,
) -> launch_seed_state.LaunchSeed | None:
    if plain_live_open(args, verdict):
        return None
    if not getattr(args, "dry_run", False):
        launch_admission.check(path)
    preserved = getattr(args, "preserve_seed_id", None)
    if preserved is not None:
        if error := preserved_seed_selection_error(args):
            raise ValueError(error)
        seed = launch_seed_state.peek(path, read_only=getattr(args, "dry_run", False))
        if record.pending_seed or seed is None or seed.seed_id != preserved:
            raise ValueError("Retained launch prompt is missing or superseded; refresh and retry")
        return seed
    explicit = getattr(args, "seed", None)
    expected_id = getattr(args, "seed_id", None)
    if explicit and expected_id:
        raise ValueError("Specify a prompt or a staged seed identity, not both")
    if explicit and not getattr(args, "dry_run", False):
        return launch_seed_state.stage(path, kind="resume", text=explicit)
    if explicit:
        return launch_seed_state.LaunchSeed("preview", "resume", explicit, 0)
    seed = (
        launch_seed_state.peek(path, read_only=True)
        if getattr(args, "dry_run", False) else launch_seed_state.peek(path)
    )
    if getattr(record, "pending_seed", None):
        if getattr(args, "dry_run", False):
            if seed is None:
                seed = launch_seed_state.LaunchSeed("preview", "new", record.pending_seed, 0)
        else:
            seed = launch_seed_state.stage(path)
    if expected_id and (seed is None or seed.seed_id != expected_id):
        raise ValueError("Staged launch prompt is missing or superseded; refresh and retry")
    return seed


def plain_live_open(args, verdict: LiveVerdict | None) -> bool:
    return bool(
        not getattr(args, "seed", None) and not getattr(args, "seed_id", None)
        and getattr(args, "preserve_seed_id", None) is None
        and verdict is not None and verdict.active
    )


def cold_start_error(
    verdict: LiveVerdict | None, kind: str = "resume", *, preserving: bool = False,
) -> str | None:
    if preserving and verdict is not None and verdict.active:
        return None
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
