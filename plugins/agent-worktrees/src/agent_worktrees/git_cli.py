"""Git collaboration CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

from pathlib import Path

from . import config as cfg, output


def _core():
    from . import __main__ as core

    return core


def _infer_worktree_id(*args, **kwargs):
    return _core()._infer_worktree_id(*args, **kwargs)


def _json_error(*args, **kwargs):
    return _core()._json_error(*args, **kwargs)


def _json_output(*args, **kwargs):
    return _core()._json_output(*args, **kwargs)


def _resolve_worktree_id(*args, **kwargs):
    return _core()._resolve_worktree_id(*args, **kwargs)


def add_parsers(sub) -> None:
    sub.add_parser("git", help="Git collaboration primitives (run 'git' for usage)")


def _git_usage() -> None:
    output.header("agent-worktrees git -- collaboration primitives")
    print("  Usage: agent-worktrees git <command> [options]")
    print()
    print("  Commands:")
    print("    sync                  Rebase the worktree branch forward onto the")
    print("                          updated remote default branch (build on top")
    print("                          of a just-merged PR). Mid-flight: no push.")
    print("    feature-branch <name> Create/update [--push] or --sync a durable")
    print("                          shared feature branch (feature/<name>).")
    print("    merge-to-feature <name>")
    print("                          Rebase + ff-merge this worktree's branch into")
    print("                          the shared feature branch and push it (the")
    print("                          delegate handoff). --no-push to stop at ff.")
    print()
    print("  Common options: [--worktree-id ID] [--config PATH] [--dry-run] [--json]")
    print()
    print("  See the 'git-collaboration' skill for the full boundary -- which git")
    print("  operations to wrap vs. run directly.")


def _git_resolve_target(rest: list[str], use_json: bool):
    """Resolve ``(config, worktree_id)`` for a git sub-group command."""
    config_arg = None
    worktree_id_arg = None
    if "--config" in rest:
        index = rest.index("--config")
        if index + 1 < len(rest):
            config_arg = rest[index + 1]
    if "--worktree-id" in rest:
        index = rest.index("--worktree-id")
        if index + 1 < len(rest):
            worktree_id_arg = rest[index + 1]
    try:
        config = cfg.load_config(Path(config_arg) if config_arg else None)
    except Exception as exc:
        if use_json:
            return None, _json_error(str(exc))
        raise
    worktree_id = _infer_worktree_id(worktree_id_arg, config)
    if not worktree_id:
        msg = "Could not determine worktree ID. Pass --worktree-id or run from inside a worktree."
        if use_json:
            return None, _json_error(msg)
        output.err(msg)
        return None, 1
    return config, _resolve_worktree_id(worktree_id)


def _git_positional(rest: list[str]) -> str | None:
    """First non-flag, non-option-value token (the <name> argument)."""
    value_flags = {"--worktree-id", "--config"}
    skip = False
    for token in rest:
        if skip:
            skip = False
            continue
        if token in value_flags:
            skip = True
            continue
        if token.startswith("-"):
            continue
        return token
    return None


def cmd_git_sync(rest: list[str]) -> int:
    if "--help" in rest or "-h" in rest:
        print(
            "Usage: agent-worktrees git sync "
            "[--worktree-id ID] [--config PATH] [--dry-run] [--json]"
        )
        return 0
    dry_run = "--dry-run" in rest
    use_json = "--json" in rest
    from . import git_collab

    ctx = output.stdout_to_stderr() if use_json else None
    if ctx is not None:
        ctx.__enter__()
    try:
        config, wid = _git_resolve_target(rest, use_json)
        if config is None:
            return wid
        ok = git_collab.sync_forward(wid, config, dry_run=dry_run)
        if use_json:
            _json_output({"worktree_id": wid, "synced": ok})
        return 0 if ok else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def cmd_git_feature_branch(rest: list[str]) -> int:
    if "--help" in rest or "-h" in rest:
        print(
            "Usage: agent-worktrees git feature-branch <name> [--push] [--sync] "
            "[--worktree-id ID] [--config PATH] [--dry-run] [--json]"
        )
        return 0
    name = _git_positional(rest)
    if not name:
        output.err("Usage: agent-worktrees git feature-branch <name> [--push] [--sync]")
        return 1
    push = "--push" in rest
    sync = "--sync" in rest
    dry_run = "--dry-run" in rest
    use_json = "--json" in rest
    if push and sync:
        output.err("--push and --sync are mutually exclusive.")
        return 1
    from . import git_collab

    ctx = output.stdout_to_stderr() if use_json else None
    if ctx is not None:
        ctx.__enter__()
    try:
        config, wid = _git_resolve_target(rest, use_json)
        if config is None:
            return wid
        ok = git_collab.manage_feature_branch(
            wid,
            config,
            name,
            push=push,
            sync=sync,
            dry_run=dry_run,
        )
        if use_json:
            _json_output({"worktree_id": wid, "feature": name, "ok": ok})
        return 0 if ok else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def cmd_git_merge_to_feature(rest: list[str]) -> int:
    if "--help" in rest or "-h" in rest:
        print(
            "Usage: agent-worktrees git merge-to-feature <name> [--no-push] "
            "[--worktree-id ID] [--config PATH] [--dry-run] [--json]"
        )
        return 0
    name = _git_positional(rest)
    if not name:
        output.err("Usage: agent-worktrees git merge-to-feature <name> [--no-push]")
        return 1
    push = "--no-push" not in rest
    dry_run = "--dry-run" in rest
    use_json = "--json" in rest
    from . import git_collab

    ctx = output.stdout_to_stderr() if use_json else None
    if ctx is not None:
        ctx.__enter__()
    try:
        config, wid = _git_resolve_target(rest, use_json)
        if config is None:
            return wid
        ok = git_collab.merge_to_feature(wid, config, name, push=push, dry_run=dry_run)
        if use_json:
            _json_output({"worktree_id": wid, "feature": name, "merged": ok})
        return 0 if ok else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def cmd_git_dispatch(argv: list[str]) -> int:
    """Route `git` sub-group verbs (git-collaboration primitives)."""
    if not argv or argv[0] in ("--help", "-h"):
        _git_usage()
        return 0 if argv else 1
    sub = argv[0]
    rest = argv[1:]
    if sub == "sync":
        return cmd_git_sync(rest)
    if sub == "feature-branch":
        return cmd_git_feature_branch(rest)
    if sub == "merge-to-feature":
        return cmd_git_merge_to_feature(rest)
    output.err(f"Unknown git subcommand: {sub}")
    _git_usage()
    return 1
