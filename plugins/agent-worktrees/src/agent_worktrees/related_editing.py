"""Local editing instructions from repository class and project membership."""

from __future__ import annotations


def local_edit_steps(
    *, name: str, repo_class: str, repo_path: str | None,
    adopted: bool, base_repo: bool,
) -> list[str]:
    if repo_class == "reference":
        return [f"Read-only (reference). Resolve the path with "
                f"`agent-worktrees repos find {name}`; do not edit it."]
    if repo_class == "singleton" or (repo_class == "worktree" and base_repo):
        loc = repo_path or f"(run `agent-worktrees repos find {name}`)"
        kindword = "base_repo enlistment" if repo_class == "worktree" else "singleton"
        return [f"Edit the anchor checkout directly at {loc} "
                f"({kindword}: one flow at a time; no `--new` worktree)."]
    if repo_class == "worktree":
        if adopted:
            return [f"Create an isolated worktree **programmatically** "
                    f"(no mux, no session): `{name} create --json` -- start "
                    f"Copilot in the returned path (or `cd` in and edit in "
                    f"your current session), then `{name} push-changes` / "
                    f"`{name} finalize`. **Never `{name} --new`** from a tool "
                    f"call: it launches an interactive tmux/psmux session for "
                    f"a human at a terminal."]
        if repo_path:
            command = f"agent-worktrees --project {name}"
            return [f"Create an isolated worktree programmatically (no mux, "
                    f"no session): `{command} create --json`, then edit in "
                    f"the returned path and use `{command} push-changes` / "
                    f"`{command} finalize`. Full project adoption and a "
                    f"per-project binstub are not required."]
        return [f"Adopt first: `agent-worktrees register {name}`, then "
                f"`{name} create --json` (never `{name} --new` -- that is the "
                f"interactive, human-only launch)."]
    return [f"Resolve the checkout with `agent-worktrees repos find {name}`."]
