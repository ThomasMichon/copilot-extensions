"""``agent-worktrees pr bar`` -- check a PR against its merge bar (:mod:`pr_bar`)."""

from __future__ import annotations

import argparse
import json

from . import output

_STATUS_WIDTH = 9


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent-worktrees pr bar",
        description=(
            "Check a PR against its merge bar: CI green, the reviewer's latest review on "
            "the head with no open or previously missed findings, no unresolved threads, "
            "no outstanding human change request, no merge conflict -- all on one head. "
            "Every list is read to its last page; anything unreadable is 'unknown', never "
            "met. Exit 0 met (or merged), 10 pending, 11 failed, 12 unknown, 2 usage."),
    )
    p.add_argument("operands", nargs="*",
                   help="<owner/name> <number>, a PR <number> in this project's repo, or a "
                        "worktree id (default: this worktree) whose active PR is checked")
    p.add_argument("--reviewer", default=None,
                   help="the reviewer whose latest review must be on the head "
                        "(default: Copilot's pull-request reviewer)")
    p.add_argument("--json", action="store_true", help="emit only the result JSON on stdout")
    p.add_argument("--config", default=None)
    return p


def _target(operands: list[str], config) -> tuple[str, int, str]:
    """``(repo slug, number, error)`` for the operands."""
    from . import tracking, worktree_identity
    from . import config as cfg
    from .pr_cli import _infer_active_repo_slug
    if len(operands) == 2 and "/" in operands[0] and operands[1].isdigit():
        return operands[0], int(operands[1]), ""
    if len(operands) == 1 and operands[0].isdigit():
        slug = _infer_active_repo_slug(config)
        return (slug or "", int(operands[0]),
                "" if slug else "Couldn't tell this project's repo; pass <owner/name> <number>.")
    if len(operands) > 1:
        return "", 0, "Expected <owner/name> <number>, a PR number, or a worktree id."
    wid = worktree_identity._infer_worktree_id(operands[0] if operands else None, config)
    if not wid:
        return "", 0, "Not in a worktree; pass <owner/name> <number> or a worktree id."
    try:
        record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    except Exception as exc:
        return "", 0, f"No readable tracking record for '{wid}': {exc}"
    active = record.active_pr()
    if active is None or not active.number:
        return "", 0, f"Worktree '{wid}' has no tracked PR with a number."
    return active.repo or record.repo or "", int(active.number), ""


def cmd_pr_bar(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    from pathlib import Path

    from . import config as cfg
    from . import pr_bar, providers
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
    except Exception as exc:
        output.err(str(exc))
        return 2
    slug, number, error = _target(args.operands, config)
    if error or "/" not in slug:
        output.err(error or f"'{slug}' isn't an owner/name repo slug.")
        return 2
    prcfg = config.default_repo.pr
    if (prcfg.provider or "github") != "github":
        output.err(f"pr bar reads GitHub pull requests; this repo's provider is '{prcfg.provider}'.")
        return 2
    host = providers.get_provider("github").authority_endpoint(getattr(prcfg, "api_base", "") or "")
    try:
        token = providers.account_token_for_slug(slug, prcfg)
    except Exception as exc:
        output.err(f"Couldn't resolve the account for {slug}: {exc}")
        return 12
    snap = pr_bar.read_github(slug, number, host=host, token=token)
    bar = pr_bar.evaluate(snap, reviewer=args.reviewer or pr_bar.COPILOT_REVIEWER)
    if args.json:
        print(json.dumps(bar.to_dict(), indent=2))
    else:
        print(f"{slug}#{number} at {bar.head[:9] or '?'} ({bar.state or '?'}): {bar.verdict}")
        for c in bar.clauses:
            detail = c.evidence or c.error
            print(f"  {('[' + c.status + ']').ljust(_STATUS_WIDTH)} {c.id}: {detail}")
    return pr_bar.EXIT.get(bar.verdict, 12)
