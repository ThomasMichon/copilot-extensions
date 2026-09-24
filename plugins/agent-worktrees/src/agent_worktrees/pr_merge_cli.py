"""``agent-worktrees pr-merge`` -- mechanical extraction from ``pr_cli.py``.

This module exists only to keep ``pr_cli.py`` under its module-size cap.
The functions below were moved verbatim, with no behavior change.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as cfg
from . import output
from . import pr_config
from .pr_cli import (
    _classify_pr_operands,
    _infer_active_repo_slug,
    _tracked_pr_head_evidence,
)


def _core():
    """Lazily resolve ``agent_worktrees.__main__`` -- see ``pr_cli._core``."""
    from . import __main__ as core

    return core


def _pr_merge_usage() -> None:
    out = sys.stderr
    print("Usage: <project> pr-merge <owner/name> <pr> [options]", file=out)
    print("       <project> pr-merge <owner/name> --all [options]", file=out)
    print(file=out)
    print("Signal merge consent on an APPROVED PR by applying the repo's", file=out)
    print("merge-consent label (the .copilot-extensions/agent-worktrees/config.yaml binding", file=out)
    print(
        "automerge_label; multi-machine system: auto-merge). Applies by default; it never",
        file=out,
    )
    print("merges -- the review gate still decides. Only eligible PRs are", file=out)
    print("touched (approved at head, mergeable, not draft/WIP, no hold label,", file=out)
    print("targeting the default branch).", file=out)
    print(file=out)
    print("  <pr>            Consent to one PR (the author path).", file=out)
    print("  --all           Sweep every open PR (transition-helper mode).", file=out)
    print("  --now           (submitter-self-merge repos only) merge <pr>", file=out)
    print("                  directly now (squash). Refused where the submitter", file=out)
    print("                  does not self-merge -- use pr-watch/pr-status there.", file=out)
    print("  --dry-run       Preview classification only; apply nothing.", file=out)
    print("  --loop          (sweep) Repeat until no PR remains eligible.", file=out)
    print("  --interval S    (sweep+loop) Seconds between passes (default 30).", file=out)
    print("  --max-passes N  (sweep+loop) Cap passes (0 = unbounded).", file=out)
    print("  --json          Emit the result JSON on stdout.", file=out)
    print("  Overrides: --host URL (api base), --token TOKEN.", file=out)


def _pr_merge_print_human(summary: dict) -> None:
    mode = "APPLY" if summary["apply"] else "preview (dry-run)"
    output_line = (
        f"pr-merge [{mode}] {summary['repo']}: {summary['open']} open, "
        f"{summary['eligible']} eligible for auto-merge"
    )
    print(output_line, file=sys.stderr)
    for d in summary["decisions"]:
        if d["action"] == "apply":
            if not summary["apply"]:
                mark = "+ auto-merge"
            elif d.get("applied"):
                mark = "APPLIED"
            else:
                mark = f"FAILED ({d.get('error', '')})"
        elif d["action"] == "already":
            mark = "already"
        else:
            mark = f"skip: {d.get('reason', '')}"
        print(f"  #{d['pr']:<6} {mark:<14} {d.get('title', '')}", file=sys.stderr)


def _pr_merge_now(args, prcfg, flow, *, apply: bool) -> int:
    """Perform (or preview) a submitter-direct merge -- ``pr-merge --now``.

    Only a **pr-self-merge** repo may use this submitter-direct merge verb.
    Azure DevOps routes through its native ``request_auto_complete`` operation,
    which either arms auto-complete or completes immediately with the configured
    policy bypass. Other providers honor ``prefer_auto_merge`` (#225, default
    on): first try ``enable_auto_merge``, then fall back to ``merge_pull`` where
    auto-merge is unavailable or disabled. A provider-supplied
    ``pull_review_gate`` (currently GitHub) can additionally skip straight to
    ``merge_pull(admin=True)`` when a *live, actor-bypassable* required review
    is the only thing that would otherwise leave auto-merge armed forever
    (#3296 follow-up); a required review the actor cannot bypass remains
    provider-enforced either way.
    Any other profile is refused-with-reminder, steering the agent to the
    sanctioned wait/consent path.
    Returns a shell exit code (0 success, 1 merge failure, 2 refusal/usage).
    """
    import json as _json

    from . import pr_contract as pc
    from .providers import (
        ProviderError,
        account_token_for_slug,
        actor_viewer_permission,
        get_provider,
    )

    if args.sweep:
        output.err("pr-merge --now: name a single PR number (not --all).")
        return 2

    # Only submitter-self-merge repos self-merge. Refuse elsewhere with a
    # reminder that names the sanctioned path (never a raw provider merge).
    if flow.profile != pc.PROFILE_PR_SELF_MERGE:
        reason = (
            "--now performs a direct submitter self-merge; this repo's PR-flow "
            f"profile is '{flow.profile}', which does not self-merge"
        )
        rem = pc.pr_reminder(flow, "pr-merge", ok=False, reason=reason)
        if args.json:
            print(
                _json.dumps(
                    {
                        "repo": args.repo,
                        "pr": args.pr,
                        "error": "--now not applicable to this repo's flow",
                        "flow_profile": flow.profile,
                        "merge_mode": flow.merge_mode,
                        "applied": False,
                        "reminder": rem.as_dict(),
                    }
                )
            )
        else:
            output.err(f"pr-merge --now: {reason}. Nothing merged.")
            print(rem.text(), file=sys.stderr)
        return 2

    provider = get_provider(getattr(prcfg, "provider", "gitea") or "gitea")
    base = (args.host or getattr(prcfg, "api_base", "") or "").strip()
    prefer_auto = bool(getattr(prcfg, "prefer_auto_merge", True))

    if not apply:  # --dry-run: preview only, merge nothing.
        rem = pc.pr_reminder(flow, "pr-merge", ok=True)
        would = (
            "request CI-gated native auto-merge (fallback: direct squash-merge)"
            if prefer_auto
            else "squash-merge directly (submitter-direct)"
        )
        if args.json:
            print(
                _json.dumps(
                    {
                        "repo": args.repo,
                        "pr": args.pr,
                        "action": "dry-run",
                        "would": would,
                        "prefer_auto_merge": prefer_auto,
                        "flow_profile": flow.profile,
                        "applied": False,
                        "reminder": rem.as_dict(),
                    }
                )
            )
        else:
            output.ok(
                f"pr-merge --now (dry-run): would {would} for PR #{args.pr} in "
                f"{args.repo}. Nothing merged."
            )
            print(rem.text(), file=sys.stderr)
        return 0

    tok = args.token if args.token is not None else account_token_for_slug(args.repo, prcfg)

    # General repo comprehension: this repo's *config* selects pr-self-merge
    # (a maintainer's choice), but that never implies the identity running
    # THIS command holds merge rights -- a repo with several maintainers and
    # outside contributors needs the actor's live permission checked, not
    # assumed. Fail-open on an unknown/failed read (None): only a confident
    # read-only/no-access verdict (False) refuses. A maintainer is never
    # blocked by a permission-read hiccup; a contributor is never told to
    # self-merge a PR they cannot actually merge.
    live_permission = actor_viewer_permission(provider, args.repo, api_base=base, token=tok,)
    authority = pc.actor_merge_authority(live_permission)
    if authority is False:
        reason = (
            "a live permission check found the acting identity does not have "
            f"write access to {args.repo} (reports '{live_permission}'). This "
            "repo's config selects pr-self-merge, but that authorizes "
            "maintainers, not every submitter -- a contributor's PR must wait "
            "for a maintainer to review and merge it"
        )
        rem = pc.pr_reminder_no_actor_authority(flow, reason=reason)
        if args.json:
            print(
                _json.dumps(
                    {
                        "repo": args.repo,
                        "pr": args.pr,
                        "error": "actor lacks live merge authority",
                        "viewer_permission": live_permission,
                        "flow_profile": flow.profile,
                        "applied": False,
                        "reminder": rem.as_dict(),
                    }
                )
            )
        else:
            output.err(f"pr-merge refused: {reason}. Nothing merged.")
            print(rem.text(), file=sys.stderr)
        return 2

    # Azure DevOps exposes completion through request_auto_complete rather than
    # the GitHub-oriented enable_auto_merge / merge_pull interfaces.
    if provider.name == "azure-devops":
        try:
            err = provider.request_auto_complete(
                args.repo,
                args.pr,
                api_base=base,
                token=tok,
                automerge_label=getattr(prcfg, "automerge_label", ""),
                squash=getattr(prcfg, "squash", True),
                delete_source_branch=getattr(prcfg, "delete_source_branch", True),
                bypass_policy=getattr(prcfg, "bypass_policy", False),
                bypass_reason=getattr(prcfg, "bypass_reason", ""),
            )
        except ProviderError as exc:
            err = str(exc)
        if err:
            rem = pc.pr_reminder(
                flow,
                "pr-merge",
                ok=False,
                reason="the Azure DevOps completion request failed",
            )
            if args.json:
                print(
                    _json.dumps(
                        {
                            "repo": args.repo,
                            "pr": args.pr,
                            "action": "auto-complete",
                            "applied": False,
                            "error": err,
                            "flow_profile": flow.profile,
                            "reminder": rem.as_dict(),
                        }
                    )
                )
            else:
                output.err(
                    f"pr-merge --now: failed to complete PR #{args.pr} in {args.repo}: {err}"
                )
                print(rem.text(), file=sys.stderr)
            return 1
        rem = pc.pr_reminder(flow, "pr-watch", ok=True)
        if args.json:
            print(
                _json.dumps(
                    {
                        "repo": args.repo,
                        "pr": args.pr,
                        "action": "auto-complete",
                        "applied": True,
                        "merged": False,
                        "flow_profile": flow.profile,
                        "reminder": rem.as_dict(),
                    }
                )
            )
        else:
            output.ok(
                f"pr-merge --now: requested Azure DevOps completion for PR "
                f"#{args.pr} in {args.repo}."
            )
            print(rem.text(), file=sys.stderr)
        return 0

    # Other providers prefer native CI-gated auto-merge (so the merge waits on
    # required checks) and fall back to an immediate self-merge only where the
    # provider offers no auto-merge or it can't be armed (#225).
    #
    # One case must skip straight past auto-merge instead: a live, required
    # review that the acting identity can actually bypass. GitHub's native
    # auto-merge arms successfully even when a required review is the very
    # thing blocking the merge -- it queues indefinitely rather than
    # refusing -- so a pr-self-merge repo whose sole maintainer will never
    # supply that second review would otherwise sit "armed" forever (#3296
    # follow-up: discovered landing a real-world case with a live ruleset
    # requiring 1 approving review but granting the acting Maintainer
    # pull-request-scoped bypass rights). Only a provider that
    # exposes ``pull_review_gate`` (currently GitHub) is checked; other
    # providers' auto-merge already means what it says.
    bypass_review_gate = False
    if prefer_auto and hasattr(provider, "pull_review_gate"):
        try:
            review_required, bypassable = provider.pull_review_gate(
                args.repo, args.pr, api_base=base, token=tok,
            )
        except ProviderError:
            review_required, bypassable = False, None
        bypass_review_gate = bool(review_required and bypassable)

    auto_armed = False
    if prefer_auto and not bypass_review_gate:
        try:
            auto_err = provider.enable_auto_merge(
                args.repo,
                args.pr,
                squash=True,
                api_base=base,
                token=tok,
            )
        except ProviderError as exc:
            auto_err = str(exc)
        auto_armed = not auto_err

    if auto_armed:
        # Auto-merge is armed -- the PR is NOT merged yet; it lands when checks
        # pass. Steer the agent to watch for the merge, not to finalize.
        rem = pc.pr_reminder(flow, "pr-watch", ok=True)
        if args.json:
            print(
                _json.dumps(
                    {
                        "repo": args.repo,
                        "pr": args.pr,
                        "action": "auto-merge",
                        "applied": True,
                        "merged": False,
                        "flow_profile": flow.profile,
                        "reminder": rem.as_dict(),
                    }
                )
            )
        else:
            output.ok(
                f"pr-merge --now: armed CI-gated auto-merge on PR #{args.pr} in "
                f"{args.repo} (squash). It merges when required checks pass -- "
                f"`pr-watch` wakes you on merge or regression."
            )
            print(rem.text(), file=sys.stderr)
        return 0

    try:
        err = provider.merge_pull(
            args.repo,
            args.pr,
            squash=True,
            admin=bypass_review_gate or not flow.review_blocking,
            api_base=base,
            token=tok,
        )
    except ProviderError as exc:
        err = str(exc)

    if err:
        rem = pc.pr_reminder(
            flow, "pr-merge", ok=False, reason="the direct merge did not complete"
        )
        if args.json:
            print(
                _json.dumps(
                    {
                        "repo": args.repo,
                        "pr": args.pr,
                        "action": "merge",
                        "applied": False,
                        "error": err,
                        "flow_profile": flow.profile,
                        "reminder": rem.as_dict(),
                    }
                )
            )
        else:
            output.err(f"pr-merge --now: failed to merge PR #{args.pr} in {args.repo}: {err}")
            print(rem.text(), file=sys.stderr)
        return 1

    rem = pc.pr_reminder(flow, "pr-merge", state=pc.PR_STATE_MERGED, ok=True)
    if args.json:
        print(
            _json.dumps(
                {
                    "repo": args.repo,
                    "pr": args.pr,
                    "action": "merge",
                    "applied": True,
                    "bypassed_review_gate": bypass_review_gate,
                    "flow_profile": flow.profile,
                    "reminder": rem.as_dict(),
                }
            )
        )
    else:
        if bypass_review_gate:
            output.ok(
                f"pr-merge --now: squash-merged PR #{args.pr} in {args.repo} "
                f"(submitter-direct, bypassing a required review your identity "
                f"is authorized to bypass). Run `finalize` to clean up the "
                f"worktree."
            )
        else:
            output.ok(
                f"pr-merge --now: squash-merged PR #{args.pr} in {args.repo} "
                f"(submitter-direct). Run `finalize` to clean up the worktree."
            )
        print(rem.text(), file=sys.stderr)
    return 0


def cmd_pr_merge_dispatch(argv: list[str]) -> int:
    """Route `pr-merge` -- signal merge consent (apply the consent label).

    The pure eligibility classifier lives in :mod:`agent_worktrees.pr_contract`
    (``classify_state``); the apply/sweep orchestration in
    :mod:`agent_worktrees.pr_merge`; the label-apply in the provider. The
    consent-label vocabulary is the repo's PR binding (``automerge_label`` etc.).
    """
    import json as _json

    from . import pr_contract as pc
    from . import pr_merge as pm
    from .providers import ProviderError

    if argv and argv[0] in ("--help", "-h", "help"):
        _pr_merge_usage()
        return 0

    p = argparse.ArgumentParser(prog="pr-merge", add_help=True)
    p.add_argument(
        "operands",
        nargs="*",
        metavar="[repo] [pr]",
        help="repo slug -- owner/name or ADO project/repo (optional; "
        "inferred from the active project) -- and/or PR number, "
        "in any order",
    )
    p.add_argument(
        "--all",
        action="store_true",
        dest="sweep",
        help="sweep every open PR (transition-helper mode)",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="preview classification only; apply nothing"
    )
    p.add_argument(
        "--loop", action="store_true", help="(sweep) repeat until no PR remains eligible"
    )
    p.add_argument("--interval", type=float, default=30.0)
    p.add_argument("--max-passes", type=int, default=0, dest="max_passes")
    p.add_argument("--host", default="", help="API base URL override")
    p.add_argument("--token", default=None, help="Provider token override")
    p.add_argument(
        "--now",
        action="store_true",
        help="(submitter-self-merge repos) merge the PR directly now "
        "(squash); refused where the submitter does not self-merge",
    )
    p.add_argument("--json", action="store_true", help="emit the result JSON")
    p.add_argument("--config", default=None)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    # A repo slug (contains '/') and/or a PR number (all digits) may be given in
    # any order; the slug is optional and inferred below when omitted, so a bare
    # `pr-merge <#>` is never mistaken for a repo.
    try:
        args.repo, args.pr = _classify_pr_operands(args.operands)
    except ValueError as exc:
        output.err(f"pr-merge: {exc}")
        return 2

    if args.sweep and args.pr is not None:
        output.err("pr-merge: pass either a <pr> or --all, not both")
        return 2
    if not args.sweep and args.pr is None:
        output.err("pr-merge: provide a PR number, or --all to sweep")
        return 2

    apply = not args.dry_run
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
        if args.repo is None:
            args.repo = _infer_active_repo_slug(config)
            if not args.repo:
                output.err(
                    "pr-merge: could not infer the repo from the active "
                    "project; pass an explicit repo slug"
                )
                return 2
        repo_cfg = config.default_repo
        prcfg = repo_cfg.pr
        default_branch = repo_cfg.default_branch
        flow = pr_config._pr_flow_profile(repo_cfg)

        # --now: perform the direct submitter self-merge (pr-self-merge repos).
        if args.now:
            return _pr_merge_now(args, prcfg, flow, apply=apply)

        # A submitter-self-merge repo has no consent label: bare `pr-merge` is a
        # no-op here. Refuse-with-reminder and point at the sanctioned `--now`
        # verb (handled above), NOT the human-merge/stale-anchor path below.
        if flow.profile == pc.PROFILE_PR_SELF_MERGE:
            rem = pc.pr_reminder(
                flow,
                "pr-merge",
                ok=False,
                reason="this repo merges directly (self-merge); bare pr-merge does nothing",
            )
            if args.json:
                print(
                    _json.dumps(
                        {
                            "repo": args.repo,
                            "error": "self-merge repo: use pr-merge --now",
                            "flow_profile": flow.profile,
                            "merge_mode": flow.merge_mode,
                            "applies": False,
                            "hint": "submitter-self-merge repo: merge directly with "
                            "`pr-merge <#> --now`",
                            "reminder": rem.as_dict(),
                        }
                    )
                )
            else:
                output.err(
                    "pr-merge: this repo's PR-flow profile is 'pr-self-merge' -- "
                    "the submitter merges directly. Bare pr-merge applies no "
                    "consent label here; merge now with `pr-merge <#> --now`. "
                    "Nothing applied."
                )
                print(rem.text(), file=sys.stderr)
            return 2

        if not getattr(prcfg, "automerge_label", ""):
            # No merge-consent label bound -> pr-merge cannot apply consent here.
            # Two distinct causes wear the same face; name both and point at the
            # right process for each (see the repo's PR-flow profile):
            #   (a) HUMAN-MERGE repo -- PR-gated, but a human approves + merges.
            #       pr-merge legitimately does not apply; open the PR, address
            #       review (pr-watch), and let a human merge.
            #   (b) STALE ANCHOR -- a repo that *should* have the binding, whose
            #       checkout hasn't pulled it yet. Refresh the anchor and retry;
            #       do NOT hand-merge or escalate.
            # (pr-self-merge repos are handled above; this path is human-merge.)
            msg = (
                "pr-merge: no merge-consent label (pr.automerge_label) is bound "
                f"in this repo's .copilot-extensions/agent-worktrees/config.yaml on this machine. "
                f"This repo's PR-flow profile is '{flow.profile}'. Two cases:\n"
                "  - Human-merge repo (expected): PR-gated but a HUMAN approves "
                "and merges -- pr-merge does not apply. Open the PR (create-pr), "
                "address review with pr-watch, then a human merges. Check the "
                "repo's CONTRIBUTING / review process for who merges.\n"
                "  - Stale anchor (if you EXPECTED an auto-merge label here): "
                "this checkout is likely behind -- update the anchor "
                "('test-chamber update' / 'git sync' on the anchor) so the "
                "binding is present, then retry pr-merge. Do NOT hand-merge or "
                "escalate to an admin. Nothing applied."
            )
            if args.json:
                print(
                    _json.dumps(
                        {
                            "repo": args.repo,
                            "error": "no automerge_label binding",
                            "flow_profile": flow.profile,
                            "merge_mode": flow.merge_mode,
                            "applies": False,
                            "hint": (
                                "human-merge repo: a human merges (pr-merge N/A); "
                                "OR stale anchor if an auto-merge label was "
                                "expected -- update the anchor and retry"
                            ),
                            "reminder": pc.pr_reminder(
                                flow,
                                "pr-merge",
                                ok=False,
                                reason="no merge-consent label bound",
                            ).as_dict(),
                        }
                    )
                )
            else:
                output.err(msg)
                _rem = pc.pr_reminder(
                    flow,
                    "pr-merge",
                    ok=False,
                    reason="no merge-consent label bound",
                )
                print(_rem.text(), file=sys.stderr)
            return 2

        if args.sweep:
            summary = pm.run_sweep(
                prcfg,
                args.repo,
                api_base=args.host,
                token=args.token,
                apply=apply,
                loop=args.loop,
                interval=args.interval,
                max_passes=args.max_passes,
                default_branch=default_branch,
            )
        else:
            tracked_head_sha, head_observed_at = _tracked_pr_head_evidence(
                config,
                args.repo,
                args.pr,
                prcfg.provider,
                args.host or prcfg.api_base,
            )
            row = pm.merge_one(
                prcfg,
                args.repo,
                args.pr,
                api_base=args.host,
                token=args.token,
                apply=apply,
                default_branch=default_branch,
                tracked_head_sha=tracked_head_sha,
                head_observed_at=head_observed_at,
            )
            eligible = 1 if row["action"] == "apply" else 0
            applied = 1 if row.get("applied") else 0
            failed = 1 if (row["action"] == "apply" and apply and not row.get("applied")) else 0
            summary = {
                "repo": args.repo,
                "open": 1,
                "eligible": eligible,
                "applied": applied,
                "failed": failed,
                "apply": apply,
                "decisions": [row],
            }

        if args.json:
            print(_json.dumps(summary))
        else:
            _pr_merge_print_human(summary)

        # Single-PR (author) mode: the operator named one PR and expects a
        # concrete state transition. A "skip" here means the action does NOT
        # apply to that PR (unapproved, not mergeable, draft/WIP, already merged,
        # hold label, wrong base) -- that is an ERROR, not success. A no-op must
        # never masquerade as success (issue #2779 / the defect that stranded
        # PR #2774). --all sweep mode legitimately skips ineligible PRs.
        if not args.sweep:
            row = summary["decisions"][0]
            action = row["action"]
            n = row["pr"]
            if action == "skip":
                if not args.json:
                    output.err(
                        f"pr-merge: PR #{n} in {args.repo} is not eligible for "
                        f"merge consent ({row.get('reason', 'ineligible')}); no "
                        f"consent applied. pr-merge only applies to an APPROVED, "
                        f"mergeable, non-draft PR -- nothing changed."
                    )
                return 1
            if action == "apply" and apply and not row.get("applied"):
                if not args.json:
                    output.err(
                        f"pr-merge: failed to apply merge consent to PR #{n} in "
                        f"{args.repo}: {row.get('error', 'unknown error')}"
                    )
                return 1
            if not args.json and apply:
                if action == "already":
                    output.ok(
                        f"pr-merge: merge consent already granted on PR #{n} in "
                        f"{args.repo}; the review gate will merge when satisfied."
                    )
                elif action == "apply" and row.get("applied"):
                    output.ok(
                        f"pr-merge: applied auto-merge consent to PR #{n} in "
                        f"{args.repo}; the review gate will merge when satisfied."
                    )
            return 0

        return 1 if summary["failed"] else 0
    except ProviderError as exc:
        output.err(f"pr-merge: {exc}")
        return 3
    except ValueError as exc:
        output.err(f"pr-merge: {exc}")
        return 2


