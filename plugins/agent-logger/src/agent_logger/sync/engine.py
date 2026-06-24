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
from agent_logger.sync.notify import post_notify
from agent_logger.sync.targets import build_target


def _automation_disabled() -> bool:
    """Honor an opt-out so automation contexts can skip syncing."""
    return os.environ.get("AGENT_LOGGER_SYNC_DISABLED") == "1"


def _machine(cfg: Config) -> str:
    return cfg.machine_name or detect_machine()


def _session_matches_allowlist(session_dir, allowlist: list[str]) -> bool:
    """Match a session's workspace cwd/git_root against the allowlist.

    Case-insensitive substring match. Sessions with no workspace.yaml or no
    cwd/git_root are considered matching (fail-open), to avoid silently
    dropping sessions that predate workspace metadata.
    """
    ws = session_dir / "workspace.yaml"
    if not ws.is_file():
        return True
    try:
        paths: list[str] = []
        with open(ws, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                for key in ("cwd:", "git_root:", "repository:"):
                    if line.startswith(key):
                        val = line[len(key):].strip()
                        if val:
                            paths.append(val.lower())
        if not paths:
            return True
        return any(p.lower() in path_val for path_val in paths for p in allowlist)
    except OSError:
        return True


def _included_sessions(source, allowlist: list[str]) -> set[str] | None:
    """Resolve the allowlist to a set of included session-state ids.

    Returns ``None`` when the allowlist is empty (sync everything).
    """
    if not allowlist:
        return None
    ss = source / "session-state"
    if not ss.is_dir():
        return set()
    return {
        d.name
        for d in ss.iterdir()
        if d.is_dir() and _session_matches_allowlist(d, allowlist)
    }


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
    include = _included_sessions(source, allowlist)

    if verbose:
        print(f"machine:   {machine}")
        print(f"source:    {source}")
        print(f"target:    {target.describe()}")
        if include is not None:
            print(f"allowlist: {allowlist} -> {len(include)} session(s) included")

    if not source.is_dir():
        print(f"session-sync: source not found: {source}", file=sys.stderr)
        return 1

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
        result = target.push(source, machine, include)
        if not result.ok:
            print(f"session-sync: push failed: {result.detail}", file=sys.stderr)
            return 1
        print(f"session-sync: ok {result.detail} ({result.file_count} files)")

        if prune:
            removed = target.prune(machine, cfg.sync_retention_days)
            if removed:
                print(f"session-sync: pruned {removed} old session(s)")

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="session-sync", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run one sync pass")
    p_run.add_argument("--dry-run", action="store_true", help="show what would happen")
    p_run.add_argument("--prune", action="store_true", help="prune old sessions after sync")
    p_run.add_argument("--verbose", action="store_true", help="verbose output")

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

    sub.add_parser("status", help="show resolved sync configuration")
    sub.add_parser("doctor", help="check the target is reachable/usable")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config()

    if args.command == "run":
        return run_sync(
            cfg, dry_run=args.dry_run, prune=args.prune, verbose=args.verbose
        )
    if args.command == "push":
        return run_push(
            cfg, source=args.source, machine=args.machine, verbose=args.verbose
        )
    if args.command == "status":
        return do_status(cfg)
    if args.command == "doctor":
        return do_doctor(cfg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
