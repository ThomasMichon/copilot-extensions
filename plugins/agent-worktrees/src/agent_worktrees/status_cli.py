"""Worktree status read surface extracted from ``__main__``."""

from __future__ import annotations

import argparse

from . import config as cfg
from . import git_ops, output, profile_assignment, sessions, tracking


def _core():
    from . import __main__ as core

    return core


def add_parsers(sub) -> None:
    # status
    p = sub.add_parser(
        "status",
        help="Show worktree git status (read); annotate this worktree's disposition (write)",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument(
        "--mux-details",
        action="store_true",
        help="Include mux session attached/detached status (JSON only)",
    )
    p.add_argument(
        "--summary",
        default=None,
        help="Set this worktree's one-line disposition summary (write mode)",
    )
    p.add_argument(
        "--title",
        default=None,
        help="Set this worktree's title -- the Picker's headline label "
        "(write mode). Use when the worktree's focus changes. Keep "
        "it short (<=30 chars; longer is truncated) so it fits the "
        "status bar / Picker rows -- put detail in --summary.",
    )
    p.add_argument(
        "--follow-up",
        dest="follow_up",
        action="store_true",
        help="Flag this worktree as having actionable follow-ups (write mode)",
    )
    p.add_argument(
        "--resolved",
        action="store_true",
        help="Clear the follow-up flag -- this worktree is resolved (write mode)",
    )
    p.add_argument(
        "--worktree-id",
        default=None,
        help="Target worktree id for write mode (default: inferred from CWD)",
    )
    p.add_argument(
        "--history",
        action="store_true",
        help="Show this worktree's disposition history (summary/title "
        "changes over time); read mode, honors --json / --limit",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="With --history, show only the most recent N entries",
    )


def cmd_status(args: argparse.Namespace) -> int:
    # worktree-status-core: write mode. When any disposition flag is present,
    # annotate THIS worktree (from CWD) and return -- leaving the fleet-wide
    # read path (`status` / `status --json`, no write flags) untouched.
    _summary = getattr(args, "summary", None)
    _title = getattr(args, "title", None)
    _fu = getattr(args, "follow_up", False)
    _res = getattr(args, "resolved", False)
    if _fu and _res:
        output.err("Pass only one of --follow-up / --resolved.")
        return 1
    _follow = True if _fu else (False if _res else None)
    if _summary is not None or _title is not None or _follow is not None:
        return _core()._cmd_status_write(args, summary=_summary, title=_title, follow_up=_follow)

    # worktree-status-core: history read mode (per-worktree), orthogonal to the
    # fleet read below.
    if getattr(args, "history", False):
        return _core()._cmd_status_history(args)

    profile_assignment.maintain()
    tracking_path = cfg.tracking_dir()

    records = tracking.list_records(tracking_path)
    if not records:
        if args.json:
            _core()._json_output({"worktrees": []})
            return 0
        print("No tracked worktrees.")
        return 0

    config = cfg.load_config()
    repo = config.default_repo

    # Scan for live sessions to feed into classification
    session_ctx = sessions.scan_sessions_fast(records)
    active_paths = _core()._build_active_paths(records, session_ctx)

    # Mux status (batch query if requested)
    mux_map: dict[str, sessions.MuxInfo] = {}
    if getattr(args, "mux_details", False):
        wt_ids = [rec.worktree_id for rec in records]
        mux_map = sessions.mux_status_many(wt_ids)

    # worktree-status-core / #3070: this fleet-wide read used to fetch once PER
    # WORKTREE via `classify_worktree(..., fetch=True, ...)` -- N real, sequential
    # `git fetch` subprocesses (each individually bounded, but with no shared
    # budget) for what is, per repo, the *same* remote-tracking refs. Those refs
    # live in the `.git` common dir a repo's worktrees all share, so one fetch
    # against the **anchor** (mirrors `cmd_sync`'s own "one fetch refreshes the
    # shared upstream ref for every worktree of this repo" pattern) is sufficient
    # -- and only when the repo's own freshness ledger
    # (`tracking.is_repo_fetch_fresh` / `record_repo_fetch_confirmed`, the same
    # ledger the resident status-monitor already relies on for this dedup in
    # `session_catalog.py::_maybe_refresh_repo_freshness`) says it's actually
    # stale, so this command, `cmd_sync`, and the monitor never redundantly
    # re-fetch a repo any of the others just refreshed.
    _distinct_repos = {r.repo for r in records if r.repo}
    if any(not tracking.is_repo_fetch_fresh(r) for r in _distinct_repos):
        if git_ops.has_remote(repo.remote, cwd=repo.anchor):
            try:
                git_ops.fetch(repo.remote, cwd=repo.anchor)
            except Exception:
                pass
            else:
                for _r in _distinct_repos:
                    tracking.record_repo_fetch_confirmed(_r)

    results: list[dict] = []
    for rec in records:
        info = git_ops.classify_worktree(
            rec.worktree_path,
            rec.branch,
            fetch=False,
            remote=repo.remote,
            default_branch=repo.default_branch,
            active_paths=active_paths,
        )
        info = _core()._apply_tracking_override(rec, info)
        result_entry = _core()._worktree_to_dict(
            rec,
            state_info=info,
            mux_info=mux_map.get(rec.worktree_id),
            session_ctx=session_ctx,
        )
        # Add display helpers for table output
        short_id = rec.worktree_id[-4:] if len(rec.worktree_id) > 4 else rec.worktree_id
        result_entry["short_id"] = short_id
        display_title = rec.title if (rec.title and rec.title != "null") else None
        if not display_title:
            norm = _core()._normalize_path(rec.worktree_path)
            display_title = session_ctx.latest_summary.get(norm)
        if not display_title:
            display_title = info.title or "(none)"
        result_entry["title"] = display_title
        results.append(result_entry)

    if args.json:
        _core()._json_output({"worktrees": results})
        return 0

    # Table output
    STATE_COLORS = {
        "active": "36",
        "unused": "2",
        "completed": "32",
        "wip": "33",
        "dirty": "31",
        "gone": "31",
        "orphan": "35",
    }

    print()
    print(f"🌳 {config.repo_name.replace('-', ' ').title()} -- Worktree Status")
    print()
    print(f"{'ID':<6} {'State':<11} {'Ahead':<7} {'Behind':<8} Title")
    print(f"{'─' * 5:<6} {'─' * 10:<11} {'─' * 6:<7} {'─' * 7:<8} {'─' * 30}")

    for r in results:
        color = STATE_COLORS.get(r.get("state", ""), "0")
        state_str = (
            f"\033[{color}m{r.get('state', ''):<11}\033[0m"
            if output._COLOR
            else f"{r.get('state', ''):<11}"
        )
        print(
            f"{r['short_id']:<6} {state_str} {r.get('ahead', ''):<7} "
            f"{r.get('behind', ''):<8} {r['title']}"
        )

    # Summary
    unused_count = sum(1 for r in results if r.get("state") == "unused")
    completed_count = sum(1 for r in results if r.get("state") == "completed")
    cleanable = unused_count + completed_count

    print()
    if cleanable > 0:
        parts = []
        if completed_count:
            parts.append(f"{completed_count} completed")
        if unused_count:
            parts.append(f"{unused_count} unused")
        print(f"{cleanable} worktree(s) can be cleaned up ({', '.join(parts)}).")
    else:
        print("All worktrees are active.")

    return 0
