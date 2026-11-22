"""Package ordinary target-local execution without contacting the target."""

from __future__ import annotations

from .remote_seed_launch import shell_command


def plan(
    project: str, alias: str, shell: str, args: list[str], *,
    seed: str | None = None,
) -> dict:
    forwarded = list(args)
    if "--require-foreground-completion" not in forwarded:
        forwarded.append("--require-foreground-completion")
    if seed is not None:
        if "--stage-launch-seed" not in forwarded:
            forwarded.append("--stage-launch-seed")
        forwarded.append("--seed=" + seed)
    worktree_id = (
        forwarded[forwarded.index("--worktree-id") + 1]
        if "--worktree-id" in forwarded else None
    )
    return {
        "action": "remote",
        "ssh_alias": alias,
        "remote_command": shell_command(shell, [project, *forwarded]),
        "worktree_id": worktree_id,
        "remote_terminal_handoff_version": 1,
    }
