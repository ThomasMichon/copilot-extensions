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
        # The supplied config's project (--config), never the ambient one.
        record = tracking.load_record(cfg.tracking_dir(getattr(config, "repo_name", None)) / f"{wid}.yaml")
    except Exception as exc:
        return "", 0, f"No readable tracking record for '{wid}': {exc}"
    active = record.active_pr()
    if active is None or not active.number:
        return "", 0, f"Worktree '{wid}' has no tracked PR with a number."
    return active.repo or record.repo or "", int(active.number), ""


def read_target(operands: list[str], config_path: str | None, verb: str) -> tuple[int, dict]:
    """Resolve the PR the operands name and read it once through its repo's own
    ``PRProvider`` (``get_bar_snapshot``): ``(0, context)``, or ``(exit code, {})``
    after reporting why. Shared by ``pr bar`` and ``pr rounds``."""
    from pathlib import Path

    from . import config as cfg
    from . import pr_bar, pr_config, providers
    try:
        config = cfg.load_config(Path(config_path) if config_path else None)
    except Exception as exc:
        output.err(str(exc))
        return 2, {}
    slug, number, error = _target(operands, config)
    if error or "/" not in slug:
        output.err(error or f"'{slug}' isn't an owner/name repo slug.")
        return 2, {}
    resolution = pr_config.resolve_repo_config_for_slug(config, slug)
    if not resolution.resolved:
        output.err(f"pr {verb}: {slug!r} isn't a registered repo this machine can resolve a PR "
                   "binding for; register it (agent-worktrees repos add) so its own provider, "
                   "host and token are used. Refusing to use another repo's binding.")
        return 2, {}
    prcfg = resolution.repo_config.pr
    name = prcfg.provider or "github"
    api_base = getattr(prcfg, "api_base", "") or ""
    try:
        token = providers.account_token_for_slug(slug, prcfg)
    except Exception as exc:
        output.err(f"Couldn't resolve the account for {slug}: {exc}")
        return 12, {}
    try:  # the read goes through the repo's PRProvider; a provider without one reads unknown
        snap = providers.get_provider(name).get_bar_snapshot(slug, number, api_base=api_base, token=token)
    except Exception as exc:
        snap = pr_bar.unsupported(slug, number, name)
        snap.errors["pr"] = f"the {name} provider's merge-bar read failed: {exc}"
    return 0, {"slug": slug, "number": number, "snap": snap, "name": name, "api_base": api_base,
               "token": token, "resolution": resolution, "prcfg": prcfg}


def cmd_pr_bar(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    from . import pr_bar, pr_config, providers
    code, ctx = read_target(args.operands, args.config, "bar")
    if code:
        return code
    slug, number, snap, name = ctx["slug"], ctx["number"], ctx["snap"], ctx["name"]
    api_base, token, resolution, prcfg = ctx["api_base"], ctx["token"], ctx["resolution"], ctx["prcfg"]
    try:  # the acting identity's role-specific policy, as pr-watch resolves it
        actor_flow = pr_config.resolve_actor_pr_flow(resolution.repo_config, slug, token=token)
        policy_cfg, review_blocking = actor_flow.pr_config, pr_config.actor_review_blocking(actor_flow)
        # Demoted by live authority (e.g. a read-only contributor in a self-merge repo):
        # a real human's approval is required, as pr-watch waits for one.
        demoted = actor_flow.resolution == "actor-authority"
    except Exception:
        policy_cfg, demoted = prcfg, False  # the configured base policy
        review_blocking = bool(getattr(prcfg, "review_blocking", True))
    policy = {"approval_required": bool(getattr(policy_cfg, "approval_required", True)) or demoted,
              "human_approval_required": demoted,
              "hold_labels": tuple(getattr(policy_cfg, "hold_labels", ()) or ()),
              "wip_title_prefixes": tuple(getattr(policy_cfg, "wip_title_prefixes", ()) or ()),
              "review_blocking": review_blocking}
    gate = getattr(providers.get_provider(name), "pull_review_gate", None)
    if snap.review_decision == "REVIEW_REQUIRED" and gate is not None:
        try:  # the provider's live answer to "may this actor bypass the required review?"
            policy["review_bypass"] = gate(slug, number, api_base=api_base, token=token)[1] is True
        except Exception:
            policy["review_bypass"] = False
    bar = pr_bar.evaluate(snap, reviewer=args.reviewer or pr_bar.COPILOT_REVIEWER, policy=policy)
    if args.json:
        print(json.dumps(bar.to_dict(), indent=2))
    else:
        print(f"{slug}#{number} at {bar.head[:9] or '?'} ({bar.state or '?'}): {bar.verdict}")
        for c in bar.clauses:
            detail = c.evidence or c.error
            print(f"  {('[' + c.status + ']').ljust(_STATUS_WIDTH)} {c.id}: {detail}")
    return pr_bar.EXIT.get(bar.verdict, 12)
