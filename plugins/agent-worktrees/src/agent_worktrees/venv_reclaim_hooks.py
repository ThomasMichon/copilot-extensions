"""Lifecycle hooks and CLI for checkout-local test-venv reclamation.

:mod:`venv_reclaim` owns recognition and fail-closed removal. This module
connects it to ``clean-test-venvs``, finalization, cleanup and session end.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any

from . import output, venv_reclaim

# A kept checkout's own session may still be preparing a test venv.
SETTLE_SECS = 60.0
SESSION_END_BUDGET_ENV = "AGENT_WORKTREES_SESSION_END_TEST_VENV_BUDGET_SECS"
_SESSION_END_DEFAULT_BUDGET = 3.0


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "clean-test-venvs",
        help="Remove rebuildable checkout-local test venvs (.test-venvs/.test-runs) that no live process uses",
    )
    p.add_argument("--worktree-dir", default=None,
                   help="Checkout to clean (default: the checkout containing the current directory)")
    p.add_argument("--dry-run", action="store_true", help="Report what would be removed without removing anything")
    p.add_argument("--json", action="store_true", help="Emit the report as JSON")
    p.add_argument("--min-age-secs", type=float, default=0.0,
                   help="Leave venvs active within this many seconds (default 0)")


def _checkout_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def cmd_clean_test_venvs(args: argparse.Namespace) -> int:
    """Remove recognized disposable test venvs in one checkout."""
    target = Path(args.worktree_dir).resolve() if args.worktree_dir else _checkout_root(Path.cwd().resolve())
    if target is None or not target.is_dir():
        message = "no checkout found; pass --worktree-dir"
        if args.json:
            return output._json_error(message, 2)
        output.err(message)
        return 2
    report = venv_reclaim.clean_test_venvs(target, dry_run=args.dry_run, min_age_secs=args.min_age_secs)
    if args.json:
        output._json_output(report)
        return 0
    for item in [*report["venvs"], *report["roots"]]:
        detail = f" -- {item['detail']}" if item.get("detail") else ""
        print(f"  {item['status']}: {item['path']}{detail}")
    verb = "Would remove" if args.dry_run else "Removed"
    count = sum(1 for v in report["venvs"] if v["status"] == "would_remove") if args.dry_run else report["removed"]
    print(f"{verb} {count} test venv(s) in {target}; {report['remaining']} left in place.")
    return 0


def data_blocker(worktree_path: str | Path) -> str | None:
    """Test-root content other than test venvs holds whole-checkout disposal.

    Such content is often Git-ignored run output, so the checkout's own data
    gate does not see it.
    """
    held = venv_reclaim.unrecognized_roots(worktree_path)
    if not held:
        return None
    return (
        "preserved test-root content at " + ", ".join(str(root) for root in held)
        + " is not a recognized test venv; review it with tools/reclaim_legacy_test_dirs.py"
    )


def remove_checkout_tree(path: str | Path) -> None:
    """Best-effort whole-tree removal that clears read-only bits and long paths."""
    venv_reclaim.rmtree_best_effort(path)


def removal_blocker(worktree_path: str | Path) -> str | None:
    """Reclaim test venvs before whole-checkout removal; report what holds it.

    Runs after the checkout's own process gate. Removing venvs first keeps
    Windows locks and deep paths in them from leaving a partial checkout.
    """
    report = venv_reclaim.clean_test_venvs(worktree_path)
    return venv_reclaim.disposal_blocker(report, allow_unverified=True)


def reclaim_retained(worktree_path: str, *, managed: bool) -> None:
    """Best-effort removal of rebuildable test venvs in a kept checkout."""
    if not managed or not Path(worktree_path).exists():
        return
    try:
        report = venv_reclaim.clean_test_venvs(worktree_path, min_age_secs=SETTLE_SECS)
    except OSError as exc:
        output.warn(f"Could not remove checkout-local test venvs: {exc}")
        return
    if report["removed"]:
        output.info(f"Removed {report['removed']} rebuildable test venv(s) from the kept checkout.")
    if report["remaining"]:
        output.info(
            f"{report['remaining']} test venv(s) are in use, unverified or recently active; "
            "'agent-worktrees clean-test-venvs' or a later cleanup removes them."
        )


def _budget() -> float:
    try:
        return float(os.environ.get(SESSION_END_BUDGET_ENV, str(_SESSION_END_DEFAULT_BUDGET)))
    except ValueError:
        return _SESSION_END_DEFAULT_BUDGET


def reclaim_after_session_end(wt_id: str) -> dict[str, Any] | None:
    """Bounded best-effort test-venv cleanup once a checkout's last session ends.

    Never raises. The decision and the detaching rename run under the
    tracking record lock that session registration takes, so a replacement
    session either registers first (and nothing is removed) or finds its
    old environment already renamed aside. Deletion happens after release.
    """
    budget = _budget()
    if budget <= 0:
        return None
    deadline = time.monotonic() + budget
    try:
        from . import config as cfg, tracking, tracking_session_registry

        yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        with tracking._RecordLock(yaml_path, blocking=False, require_sidecar=True) as lock:
            if not lock.acquired:
                return None
            record = tracking.load_record(yaml_path)
            path = getattr(record, "worktree_path", None)
            if not path or tracking_session_registry._record_has_open_session(record):
                return None
            report = venv_reclaim.clean_test_venvs(
                path, min_age_secs=SETTLE_SECS, deadline=deadline, detach_only=True)
        report = venv_reclaim.remove_detached(report, deadline)
        if report["removed"]:
            from . import activity

            activity.log_event(
                "session_end_test_venvs_removed",
                worktree_id=wt_id,
                removed=report["removed"],
                remaining=report["remaining"],
            )
        return report
    except Exception:
        return None


__all__ = [
    "SESSION_END_BUDGET_ENV", "SETTLE_SECS", "add_parser", "cmd_clean_test_venvs", "data_blocker",
    "reclaim_after_session_end", "reclaim_retained", "removal_blocker", "remove_checkout_tree",
]
