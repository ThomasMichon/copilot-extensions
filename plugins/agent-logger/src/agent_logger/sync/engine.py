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

from agent_logger.config import Config, load_config
from agent_logger.segmenter.platform import detect_machine
from agent_logger.sync.lock import sync_lock
from agent_logger.sync.targets import build_target


def _automation_disabled() -> bool:
    """Honor an opt-out so automation contexts can skip syncing."""
    return os.environ.get("AGENT_LOGGER_SYNC_DISABLED") == "1"


def _machine(cfg: Config) -> str:
    return cfg.machine_name or detect_machine()


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

    if verbose:
        print(f"machine:   {machine}")
        print(f"source:    {source}")
        print(f"target:    {target.describe()}")

    if not source.is_dir():
        print(f"session-sync: source not found: {source}", file=sys.stderr)
        return 1

    if dry_run:
        print(f"session-sync: would push {source} -> {target.describe()} (machine={machine})")
        return 0

    lock_file = cfg.home / "session-sync.lock"
    with sync_lock(lock_file, timeout=cfg.sync_lock_timeout) as acquired:
        if not acquired:
            print("session-sync: another sync holds the lock; skipping", file=sys.stderr)
            return 0
        result = target.push(source, machine)
        if not result.ok:
            print(f"session-sync: push failed: {result.detail}", file=sys.stderr)
            return 1
        print(f"session-sync: ok {result.detail} ({result.file_count} files)")

        if prune:
            removed = target.prune(machine, cfg.sync_retention_days)
            if removed:
                print(f"session-sync: pruned {removed} old session(s)")
    return 0


def do_status(cfg: Config) -> int:
    machine = _machine(cfg)
    target = build_target(cfg.sync_target, cfg.target_options(cfg.sync_target))
    print(f"machine:        {machine}")
    print(f"source:         {cfg.sync_source}")
    print(f"target:         {target.describe()}")
    print(f"retention_days: {cfg.sync_retention_days}")
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
    if args.command == "status":
        return do_status(cfg)
    if args.command == "doctor":
        return do_doctor(cfg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
