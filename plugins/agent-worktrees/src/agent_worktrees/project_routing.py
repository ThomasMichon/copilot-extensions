"""Project-independent argv decisions without config or process discovery."""

from __future__ import annotations

_NO_PROJECT_COMMANDS = {
    "identity-vault",
    "dev-status",
    "dev-release",
    "--version",
    "-V",
    "--help",
    "-h",
    "repos",
    "accounts",
    "forks",
    "copilot-identity",
    "related",
    "install",
    "register",
    "hook",
    "knowledge",
    "resource-producer",
    "resource-relationship",
    "reconcile-marketplaces",
    "launch-update",
    "picker",
    "doctor",
    "hygiene",
    "reap-shells",
    "status-updater",
    "status-monitor",
    "reconcile-sessions",
    "status-monitor-restart",
    "restart",
    "register-session",
    "handoff-trace",
    "session-lifecycle",
    "deregister-session",
    "session-binding",
    "session-recovery",
    "session-lineage",
    "installer-readiness",
    "bind-session",
    "bind-nudge",
    "history-digest",
    "note-handoff",
    "cancel-handoff",
    "session-role",
    "head-session",
    "worktree-lineage",
    "worktree-status-bundle",
    "worktree-status-audit",
    "conclude-session",
    "conclude-disposable",
    "link-succession",
    "config-migrate",
    "session-tail",
    "session-observations",
    "session-lock",
    "machine-context",
    "reconcile-binstubs",
    "register-project-entry",
    "activity-prune-worker",
}


def _is_no_project_invocation(args_list: list[str]) -> bool:
    """Whether this exact argv can run without resolving one project."""
    if not args_list:
        return False
    command = args_list[0]
    if command in _NO_PROJECT_COMMANDS or command.startswith("-"):
        return True
    if command == "pr-watch" and len(args_list) > 1 and args_list[1] in (
        "snapshot", "status", "threads", "deliver",
    ):
        return True
    if args_list[:2] == ["pr-watch", "checks"] and all(
        any(arg == flag or arg.startswith(flag + "=") for arg in args_list[2:])
        for flag in ("--config", "--workspace")
    ):
        return True
    if command == "pr-status" and all(
        any(arg == flag or arg.startswith(flag + "=") for arg in args_list[1:])
        for flag in ("--repo", "--pr")
    ):
        return True
    if command == "update" and any(
        arg in {"--request-status", "--recovery-status", "--recover-after-restart", "--recover-for-repair"}
        or arg.startswith("--request-status=") for arg in args_list[1:]
    ):
        return True
    if command == "update" and "--auth-report" in args_list[1:] and any(
        arg == "--auth-source" or arg.startswith("--auth-source=") for arg in args_list[1:]
    ):
        return True
    if command == "list-sessions" and "--all-projects" in args_list[1:]:
        return True
    if command == "claims" and args_list[1:3] == ["find", "pr"]:
        # Scans every adopted project's tracking store, never the CWD's own:
        # it must give the same answer from any directory.
        return True
    if command == "claims" and args_list[1:2] == ["ownership"]:
        # Strict read-only global snapshot (cmd_ownership -> scan_ownership):
        # scans every adopted project's tracking store, never the CWD's own,
        # and takes no positional selectors -- it must run from a neutral
        # directory with no active project, same as `claims find pr`.
        return True
    if command == "get" and any(
        arg == "--session-id" or arg.startswith("--session-id=") for arg in args_list[1:]
    ):
        # `get <key> --session-id <sid>` resolves its own project via the
        # session binding (cmd_get's own _activate_session_binding /
        # find_worktree_id_by_session fallback) -- a caller sitting in a
        # neutral/HOME cwd (the bare-resume case a session id exists
        # precisely to recover from) must reach that resolution instead of
        # being rejected here before cmd_get() ever runs.
        return True
    if command == "identifiers" and "--repo" in args_list[1:]:
        # `identifiers sweep --repo NAME` names its own sweep target
        # explicitly and never consults CWD -- it must work from any
        # directory (the documented live-guard invocation from an
        # unrelated repo's own pre-push hook). The bare form (no --repo)
        # still auto-resolves its target from CWD via `cfg.active_project()`,
        # so it keeps needing ordinary project resolution here.
        return True
    if command == "reconcile-plugins" and (
        "--check-update" in args_list[1:] or any(
            arg == "--repo" or arg.startswith("--repo=") for arg in args_list[1:]
        )
    ):
        return True
    return command == "config-root" and any(
        arg == "--destination" or arg.startswith("--destination=") for arg in args_list[1:]
    )
