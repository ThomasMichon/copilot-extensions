"""session-sync -- push raw Copilot session data to a configurable target.

A thin, cross-platform engine: it discovers the local session source, takes a
serialized lock, dispatches to the configured :mod:`~agent_logger.sync.targets`
target, optionally prunes the destination, and reports status. The transport
specifics live in the target classes -- the engine itself is transport-blind.

Console script: ``session-sync`` (see pyproject ``[project.scripts]``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from agent_logger.config import Config, load_config
from agent_logger.segmenter.platform import detect_machine
from agent_logger.sync.lock import sync_lock
from agent_logger.sync.meta import (
    MAX_DEFERRED_FILE_SAMPLES,
    MAX_DEFERRED_PATH_CHARS,
)
from agent_logger.sync.notify import post_notify
from agent_logger.sync.origin import classify_for_sync, effective_harness, mark_all
from agent_logger.sync.targets import build_target


def _automation_disabled() -> bool:
    """Honor an opt-out so automation contexts can skip syncing."""
    return os.environ.get("AGENT_LOGGER_SYNC_DISABLED") == "1"


def _machine(cfg: Config) -> str:
    return cfg.machine_name or detect_machine()


def _status_text(value, default: str = "(unknown)", limit: int = 512) -> str:
    if not isinstance(value, (str, int, float, bool)):
        return default
    cleaned = "".join(char for char in str(value) if char.isprintable())
    return cleaned[:limit] or default


def _included_sessions(source, allowlist: list[str],
                       fail_closed: bool = False,
                       effective: list[str] | None = None,
                       machine: str = "",
                       denylist: list[str] | None = None) -> set[str] | None:
    """Resolve the per-repo sync policy to a set of included session ids.

    Returns ``None`` when there is **no** filter at all (empty allowlist *and*
    empty denylist -- sync everything). Otherwise a session is included per
    :func:`~agent_logger.sync.origin.classify_for_sync`: denylist excludes,
    allowlist gates (when present), and an empty allowlist with a denylist is a
    catch-all for everything not denied. ``effective`` is the origin-derivation
    set (allowlist + denylist + harness repos).
    """
    if not allowlist and not denylist:
        return None
    ss = source / "session-state"
    if not ss.is_dir():
        return set()
    eff = effective if effective is not None else list(allowlist)
    included: set[str] = set()
    for d in ss.iterdir():
        if not d.is_dir():
            continue
        include, _ = classify_for_sync(d, machine, allowlist, eff,
                                       fail_closed=fail_closed,
                                       denylist=denylist)
        if include:
            included.add(d.name)
    return included


def run_sync(
    cfg: Config,
    *,
    dry_run: bool = False,
    prune: bool = False,
    verbose: bool = False,
) -> int:
    """Execute one sync pass. Returns a process exit code."""
    if _automation_disabled():
        print("session-sync: disabled via AGENT_LOGGER_SYNC_DISABLED")
        return 0

    machine = _machine(cfg)
    source = cfg.sync_source
    target = build_target(cfg.sync_target, cfg.target_options(cfg.sync_target))
    allowlist = cfg.sync_repo_allowlist
    denylist = cfg.sync_repo_denylist
    effective = effective_harness(allowlist, cfg.sync_harness_repos, denylist)
    include = _included_sessions(source, allowlist,
                                 cfg.sync_repo_allowlist_fail_closed,
                                 effective, machine, denylist)

    if verbose:
        print(f"machine:   {machine}")
        print(f"source:    {source}")
        print(f"target:    {target.describe()}")
        if include is not None:
            scope = f"allowlist={allowlist} denylist={denylist}"
            print(f"filter:    {scope} -> {len(include)} session(s) included")

    if not source.is_dir():
        print(f"session-sync: source not found: {source}", file=sys.stderr)
        return 1

    # Tag every local session with its origin (harness repo + machine) so the
    # sidecar syncs with the session and downstream daemons can route by origin.
    origin_summary = mark_all(source, machine, effective, dry_run=dry_run)
    if verbose:
        print(f"origin:    marked {origin_summary['marked']}/"
              f"{origin_summary['total']} session(s) {origin_summary['by_repo']}")

    if dry_run:
        scope = "" if include is None else f", {len(include)} session(s) match"
        print(
            f"session-sync: would push {source} -> {target.describe()} "
            f"(machine={machine}{scope})"
        )
        return 0

    lock_file = cfg.home / "session-sync.lock"
    with sync_lock(lock_file, timeout=cfg.sync_lock_timeout) as acquired:
        if not acquired:
            print("session-sync: another sync holds the lock; skipping", file=sys.stderr)
            return 0

        # On-device compaction (before the push): archive cold, in-scope,
        # untracked local sessions into the compressed store and reclaim their
        # live dirs, so the scheduled service performs the whole compaction
        # lifecycle from config -- no separate `compact` invocation needed.
        if cfg.sync_compact["enabled"]:
            from agent_logger.sync.compact import compact_local

            cres = compact_local(cfg, verbose=verbose)
            if cres.compacted:
                mb = cres.reclaimed_bytes / (1024 * 1024)
                print(
                    f"session-sync: compacted {cres.compacted} local session(s), "
                    f"reclaimed {mb:.1f} MB"
                )
            if cres.failed:
                print(
                    f"session-sync: {len(cres.failed)} compaction failure(s): "
                    f"{'; '.join(cres.failed)}",
                    file=sys.stderr,
                )

        result = target.push(source, machine, include)
        if not result.ok:
            print(f"session-sync: push failed: {result.detail}", file=sys.stderr)
            return 1
        print(f"session-sync: ok {result.detail} ({result.file_count} files)")

        if prune:
            removed = target.prune(machine, cfg.sync_retention_days)
            if removed:
                print(f"session-sync: pruned {removed} old session(s)")

        # Two-pair sync: publish the compressed archive store to
        # {machine}/archived/, compact the hub-only backlog, and reconcile away
        # the uncompressed hub duplicates.
        if cfg.sync_compact["enabled"]:
            arc = target.push_archives(cfg.compact_archive_root, machine)
            if arc.ok and arc.file_count:
                print(f"session-sync: pushed {arc.file_count} archive file(s)")
            elif not arc.ok:
                print(f"session-sync: archive push failed: {arc.detail}", file=sys.stderr)

            opts = cfg.sync_compact
            from agent_logger.sync.compact import tracked_worktree_paths

            tracked = (
                tracked_worktree_paths()
                if opts["require_untracked_worktree"]
                else None
            )
            backlog = target.compact_backlog(
                machine, opts["min_age_days"], opts["codec"],
                tracked_paths=tracked,
            )
            if backlog:
                print(f"session-sync: compacted {backlog} hub-only session(s)")

            reclaimed = target.reconcile_hub(machine)
            if reclaimed:
                print(f"session-sync: reconciled {reclaimed} hub session(s)")

        notify = cfg.sync_notify
        if notify["url"]:
            sent = post_notify(
                notify["url"],
                machine,
                bearer_token_file=notify["bearer_token_file"],
                timeout=notify["timeout"],
            )
            if verbose:
                print(f"session-sync: notify {'sent' if sent else 'failed (ignored)'}")
    return 0


def run_push(
    cfg: Config,
    *,
    source: str,
    machine: str,
    verbose: bool = False,
) -> int:
    """Push an explicit *source* directory under an explicit *machine* label.

    Unlike :func:`run_sync` (which discovers the local ``~/.copilot`` source and
    derives the machine name from the host), this lands a caller-supplied
    directory into the configured target under an arbitrary machine subpath —
    e.g. a CodeSpace's pulled ``~/.copilot`` under ``.codespaces/<name>``. The
    source must contain ``session-state/`` and/or the top-level
    ``session-store.db`` files, exactly like ``~/.copilot``.

    Used by external callers (e.g. agent-codespaces) to reuse the agent-logger
    storage pattern without importing the package. No global sync lock is taken:
    the machine namespace is disjoint from the scheduled local sync.
    """
    if _automation_disabled():
        print("session-sync: disabled via AGENT_LOGGER_SYNC_DISABLED")
        return 0

    src = Path(source).expanduser()
    if not src.is_dir():
        print(f"session-sync: source not found: {src}", file=sys.stderr)
        return 1

    target = build_target(cfg.sync_target, cfg.target_options(cfg.sync_target))

    if verbose:
        print(f"machine: {machine}")
        print(f"source:  {src}")
        print(f"target:  {target.describe()}")

    result = target.push(src, machine, None)
    if not result.ok:
        print(f"session-sync: push failed: {result.detail}", file=sys.stderr)
        return 1
    print(f"session-sync: ok {result.detail} ({result.file_count} files)")
    return 0


def do_status(cfg: Config) -> int:
    machine = _machine(cfg)
    target = build_target(cfg.sync_target, cfg.target_options(cfg.sync_target))
    print(f"machine:        {machine}")
    print(f"source:         {cfg.sync_source}")
    print(f"target:         {target.describe()}")
    print(f"retention_days: {cfg.sync_retention_days}")
    allowlist = cfg.sync_repo_allowlist
    print(f"repo_allowlist: {allowlist or '(all)'}")
    notify = cfg.sync_notify
    print(f"notify:         {notify['url'] or '(none)'}")
    latest = target.sync_status(machine)
    if not latest.supported:
        print("latest_sync:    (target does not expose status)")
    elif latest.error:
        print(f"latest_sync:    unreadable ({_status_text(latest.error)})")
    elif latest.metadata is None:
        print("latest_sync:    (none)")
    else:
        metadata = latest.metadata
        print(f"latest_sync:    {_status_text(metadata.get('last_sync_utc'))}")
        print(f"latest_status:  {_status_text(metadata.get('status'))}")
        print(f"sessions:       {_status_text(metadata.get('session_count'))}")
        deferred_count = metadata.get("deferred_file_count", 0)
        print(f"deferred_files: {_status_text(deferred_count)}")
        deferred_files = metadata.get("deferred_files")
        if isinstance(deferred_files, list):
            for path in deferred_files[:MAX_DEFERRED_FILE_SAMPLES]:
                print(
                    f"  - {_status_text(path, '(invalid)', MAX_DEFERRED_PATH_CHARS)}"
                )
        elif deferred_files is not None:
            print("  - (invalid deferred_files metadata)")
    return 0


def do_doctor(cfg: Config) -> int:
    target = build_target(cfg.sync_target, cfg.target_options(cfg.sync_target))
    print(f"target: {target.describe()}")
    result = target.doctor()
    for name, ok, detail in result.checks:
        mark = "ok " if ok else "FAIL"
        suffix = f" ({detail})" if detail else ""
        print(f"  [{mark}] {name}{suffix}")
    return 0 if result.ok else 1


def do_compact_hub(cfg: Config, *, dry_run: bool, verbose: bool) -> int:
    """Backlog pass: compact cold hub-only sessions and reconcile duplicates."""
    machine = _machine(cfg)
    opts = cfg.sync_compact
    if not opts["enabled"]:
        print("session-sync compact-hub: disabled (sync.compact.enabled=false)")
        return 0
    target = build_target(cfg.sync_target, cfg.target_options(cfg.sync_target))
    lock_file = cfg.home / "session-sync.lock"
    # Protect hub copies of sessions whose worktree is still tracked (the
    # running machine authoritatively knows its own namespace).
    from agent_logger.sync.compact import tracked_worktree_paths

    tracked = tracked_worktree_paths() if opts["require_untracked_worktree"] else None
    with sync_lock(lock_file, timeout=cfg.sync_lock_timeout) as acquired:
        if not acquired:
            print(
                "session-sync compact-hub: another sync holds the lock; skipping",
                file=sys.stderr,
            )
            return 0
        compacted = target.compact_backlog(
            machine,
            opts["min_age_days"],
            opts["codec"],
            tracked_paths=tracked,
            dry_run=dry_run,
        )
        reclaimed = target.reconcile_hub(machine, dry_run=dry_run)
    verb = "would compact" if dry_run else "compacted"
    verb2 = "would reconcile" if dry_run else "reconciled"
    print(
        f"session-sync compact-hub: {verb} {compacted} hub session(s); "
        f"{verb2} {reclaimed} duplicate(s)"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="session-sync", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run one sync pass")
    p_run.add_argument("--dry-run", action="store_true", help="show what would happen")
    p_run.add_argument("--prune", action="store_true", help="prune old sessions after sync")
    p_run.add_argument("--verbose", action="store_true", help="verbose output")
    p_run.add_argument(
        "--detach",
        action="store_true",
        help="stage the package to a temp dir and run the sync in a detached, "
        "update-safe child process with a neutral cwd (used by the "
        "session-end hook so the sync never pins the worktree or collides "
        "with an agent-logger self-update)",
    )

    p_push = sub.add_parser(
        "push",
        help="push an explicit source dir under an explicit machine label",
    )
    p_push.add_argument(
        "--source", required=True,
        help="source dir containing session-state/ and/or session-store.db",
    )
    p_push.add_argument(
        "--machine", required=True,
        help="machine label / subpath under the target root (e.g. .codespaces/<name>)",
    )
    p_push.add_argument("--verbose", action="store_true", help="verbose output")

    p_rescue = sub.add_parser(
        "rescue-push",
        help="validate and push provider-rescued session evidence",
    )
    p_rescue.add_argument(
        "--rescue-root",
        action="append",
        required=True,
        help="provider rescues/ root (repeatable)",
    )
    p_rescue.add_argument(
        "--provider",
        default="agent-containers",
        choices=("agent-containers",),
        help="rescue provider contract",
    )
    p_rescue.add_argument(
        "--target-prefix",
        default="container",
        help="filesystem-safe venue namespace prefix",
    )
    p_rescue.add_argument("--dry-run", action="store_true", help="validate without pushing")
    p_rescue.add_argument("--verbose", action="store_true", help="verbose output")

    sub.add_parser("status", help="show resolved sync configuration")
    sub.add_parser("doctor", help="check the target is reachable/usable")

    p_compact = sub.add_parser(
        "compact",
        help="archive cold on-device sessions into the compressed store",
    )
    p_compact.add_argument(
        "--dry-run", action="store_true", help="list what would be archived"
    )
    p_compact.add_argument("--verbose", action="store_true", help="verbose output")

    p_hub = sub.add_parser(
        "compact-hub",
        help="compact cold hub-only sessions in place and reconcile duplicates",
    )
    p_hub.add_argument(
        "--dry-run", action="store_true", help="count what would be compacted"
    )
    p_hub.add_argument("--verbose", action="store_true", help="verbose output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(include_repo=False)

    try:
        if args.command == "run":
            if getattr(args, "detach", False):
                from agent_logger.sync import spawn

                return spawn.spawn_detached_sync(cfg, prune=args.prune)
            return run_sync(
                cfg, dry_run=args.dry_run, prune=args.prune, verbose=args.verbose
            )
        if args.command == "push":
            return run_push(
                cfg, source=args.source, machine=args.machine, verbose=args.verbose
            )
        if args.command == "rescue-push":
            from agent_logger.sync.rescue import run_rescue_push

            return run_rescue_push(
                cfg,
                rescue_roots=args.rescue_root,
                provider=args.provider,
                target_prefix=args.target_prefix,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
        if args.command == "status":
            return do_status(cfg)
        if args.command == "doctor":
            return do_doctor(cfg)
        if args.command == "compact":
            from agent_logger.sync.compact import run_compact

            result = run_compact(cfg, dry_run=args.dry_run, verbose=args.verbose)
            if not args.dry_run:
                mb = result.reclaimed_bytes / (1024 * 1024)
                print(
                    f"session-sync compact: archived {result.compacted} "
                    f"session(s), reclaimed {mb:.1f} MB"
                )
                if result.failed:
                    print(
                        f"session-sync compact: {len(result.failed)} failed: "
                        f"{'; '.join(result.failed)}",
                        file=sys.stderr,
                    )
                    return 1
            return 0
        if args.command == "compact-hub":
            return do_compact_hub(
                cfg, dry_run=args.dry_run, verbose=args.verbose
            )
        return 2
    finally:
        # A staged child (launched via `run --detach`) removes its throwaway
        # staging dir on the way out, whatever the outcome.
        if os.environ.get("AGENT_LOGGER_SYNC_STAGED"):
            from agent_logger.sync import spawn

            spawn.cleanup_staging()


if __name__ == "__main__":
    sys.exit(main())
