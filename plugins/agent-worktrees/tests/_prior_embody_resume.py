"""Frozen prior conversation policy for explicit-intent upgrade/rollback tests."""

from agent_worktrees import sessions


def resume_target(args, record, seed, *, make_new):
    acquisition = getattr(args, "bridge_acquisition", None)
    if acquisition:
        return None if acquisition == "fresh" else acquisition
    if make_new or record is None or getattr(args, "fresh", False):
        return None
    if (seed or getattr(args, "bridge_seed_pending", False)) and not getattr(args, "resume_head", False):
        return None
    for arg in getattr(args, "copilot_args", None) or []:
        if str(arg).split("=", 1)[0] in ("--resume", "--continue"):
            return None
    return sessions.resolve_resume_target(record)
