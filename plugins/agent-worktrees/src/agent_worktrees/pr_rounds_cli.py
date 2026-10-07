"""``agent-worktrees pr rounds`` -- is a PR's review/fix loop converging? (:mod:`pr_rounds`)."""

from __future__ import annotations

import argparse
import json

from . import output


def _parser() -> argparse.ArgumentParser:
    from . import pr_rounds
    p = argparse.ArgumentParser(
        prog="agent-worktrees pr rounds",
        description=(
            "How a PR's review/fix rounds are trending: one round per head the reviewer "
            "reviewed, measured by the open plus previously missed findings it reported. "
            "Stops a loop that isn't converging. Exit 0 continue (or done), 20 plateau, "
            "21 round cap, 12 unknown, 2 usage."),
    )
    p.add_argument("operands", nargs="*",
                   help="<owner/name> <number>, a PR <number> in this project's repo, or a "
                        "worktree id (default: this worktree) whose active PR is checked")
    p.add_argument("--reviewer", default=None,
                   help="whose reviews make the rounds (default: Copilot's pull-request reviewer)")
    p.add_argument("--max-rounds", type=int, default=pr_rounds.DEFAULT_MAX_ROUNDS,
                   help=f"stop after this many rounds (default {pr_rounds.DEFAULT_MAX_ROUNDS})")
    p.add_argument("--plateau-passes", type=int, default=pr_rounds.DEFAULT_PLATEAU_PASSES,
                   help="stop when this many rounds in a row don't improve on the best before "
                        f"them (default {pr_rounds.DEFAULT_PLATEAU_PASSES}; 0 turns it off)")
    p.add_argument("--json", action="store_true", help="emit only the result JSON on stdout")
    p.add_argument("--config", default=None)
    return p


def cmd_pr_rounds(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    from . import pr_bar, pr_rounds
    from .pr_bar_cli import read_target
    if args.max_rounds < 1 or args.plateau_passes < 0:
        output.err("pr rounds: --max-rounds must be at least 1 and --plateau-passes at least 0.")
        return 2
    code, ctx = read_target(args.operands, args.config, "rounds")
    if code:
        return code
    result = pr_rounds.evaluate(ctx["snap"], reviewer=args.reviewer or pr_bar.COPILOT_REVIEWER,
                                max_rounds=args.max_rounds, plateau_passes=args.plateau_passes)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"{ctx['slug']}#{ctx['number']}: {result.verdict} -- {result.reason}")
        for i, r in enumerate(result.rounds, 1):
            metric = "?" if r["metric"] is None else r["metric"]
            print(f"  round {i}: {r['head'][:9]}  {r['at'][:19]}  findings {metric}")
    return pr_rounds.EXIT.get(result.verdict, 12)
