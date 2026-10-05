"""Tracked-PR state CLI surface extracted from ``__main__``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as cfg, output, pr_ops, tracking
from . import context_cli, pr_config


def _core():
    from . import __main__ as core

    return core


def add_parsers(sub) -> None:
    p = sub.add_parser("set-pr", help="Record PR metadata (URL/number/state) on a worktree")
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--url", default=None, help="PR URL")
    p.add_argument("--number", type=int, default=None, help="PR number")
    p.add_argument(
        "--state",
        default=None,
        choices=["creating", "open", "merged", "closed"],
        help="PR lifecycle state",
    )
    p.add_argument("--provider", default=None, help="PR provider (gitea|github|azure-devops)")
    p.add_argument("--branch", default=None, help="Feature branch name (if not already recorded)")
    p.add_argument(
        "--pr",
        type=int,
        default=None,
        help="Select which tracked PR to update by number (default: the active PR)",
    )
    p.add_argument(
        "--select-branch",
        default=None,
        dest="select_branch",
        help="Select which tracked PR to update by feature branch",
    )
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    p = sub.add_parser(
        "pr-ready",
        help="Move a PR out of draft (draft -> ready-for-review). Does NOT "
        "grant merge consent -- use pr-merge for that.",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument(
        "--repo", default=None, help="Target repo 'owner/name' for the PR (default: tracked repo)"
    )
    p.add_argument(
        "--pr", type=int, default=None, help="Select which tracked PR to release by number"
    )
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    p = sub.add_parser(
        "pr-abandon",
        help="Close a tracked PR WITHOUT merging -- a rare, deliberate action "
        "for a genuinely superseded PR. The first call always refuses with "
        "a warning naming the alternatives (rebase+force-push; whittle down "
        "to a smaller useful contribution); only a second, explicit "
        "--confirm call (after real sign-off) actually closes it.",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument(
        "--repo", default=None, help="Target repo 'owner/name' for the PR (default: tracked repo)"
    )
    p.add_argument(
        "--pr", type=int, default=None, help="Select which tracked PR to abandon by number"
    )
    p.add_argument(
        "--reason", default=None, required=True,
        help="Why this PR is being abandoned (required on every call, not "
        "just the confirmed one -- e.g. 'superseded by #1234, no remaining "
        "unique content')",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help="Proceed past the warning. Only pass this after explicit, "
        "real sign-off for THIS PR -- never on your own judgment the "
        "first time you see the warning.",
    )
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    p = sub.add_parser(
        "pr-status",
        help="Show tracked PR metadata + live verdict/conflict/merge state "
        "(reconciles against the provider; recommends pull-forward when "
        "the active PR has merged)",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument(
        "--all", action="store_true", help="List every tracked PR, not just the active one"
    )
    p.add_argument(
        "--no-live",
        action="store_true",
        dest="no_live",
        help="Skip the live provider read (tracked metadata only)",
    )
    p.add_argument(
        "--threads", action="store_true", help="Also list the PR's review comment threads"
    )
    p.add_argument(
        "--resolve-threads",
        action="store_true",
        dest="resolve_threads",
        help="Mark active comment threads resolved (implies --threads)",
    )
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    p = sub.add_parser(
        "pr-nudge",
        help="Ask this repo's bound automated reviewer (pr.reviewer, e.g. "
        "GitHub Copilot) to (re-)review the active PR. Nothing to nudge "
        "(unconfigured/unsupported) is reported, not an error.",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    p = sub.add_parser(
        "pr-complete",
        help="Reconcile the worktree after its PR merged (fast-forward past the "
        "squash-merge, or rebase to preserve new work). Distinct from finalize.",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Report the action that would be taken; change nothing",
    )
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    p = sub.add_parser(
        "pr-diff",
        help="Read the active tracked PR's current unified diff.",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    p = sub.add_parser(
        "pr-comment",
        help="Post a general (non-verdict) comment on the active tracked PR.",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("body", help="Comment text")
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    p = sub.add_parser(
        "pr-review",
        help="Publish a review verdict (approve/request-changes/comment) on "
        "the active tracked PR.",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    verdict = p.add_mutually_exclusive_group(required=True)
    verdict.add_argument(
        "--approve", action="store_const", dest="event", const="APPROVED",
        help="Approve the PR",
    )
    verdict.add_argument(
        "--request-changes", action="store_const", dest="event",
        const="CHANGES_REQUESTED", help="Request changes on the PR",
    )
    verdict.add_argument(
        "--comment", action="store_const", dest="event", const="COMMENTED",
        help="Leave a non-blocking review comment with no verdict",
    )
    p.add_argument("--body", default="", help="Review summary text")
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)


def cmd_set_pr(args: argparse.Namespace) -> int:
    core = _core()
    use_json = getattr(args, "json", False)
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
    except Exception as e:
        if use_json:
            return output._json_error(str(e))
        raise
    worktree_id = core._infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        msg = "Could not determine worktree ID. Pass it explicitly or run from inside a worktree."
        return output._json_error(msg) if use_json else (output.err(msg) or 1)
    worktree_id = core._resolve_worktree_id(worktree_id)

    result = pr_ops.set_pr(
        worktree_id,
        url=args.url,
        number=args.number,
        state=args.state,
        provider=args.provider,
        branch=args.branch,
        select_number=getattr(args, "pr", None),
        select_branch=getattr(args, "select_branch", None),
        config=config,
    )
    if (
        result.get("success")
        and result.get("state") == "open"
        and result.get("number") is not None
        and result.get("head_sha")
    ):
        try:
            record = tracking.load_record(cfg.tracking_dir() / f"{worktree_id}.yaml")
            target_pr = next(
                (
                    pr
                    for pr in record.prs
                    if pr.number == result.get("number")
                    and pr.branch == result.get("branch")
                    and pr.repo == result.get("repo")
                ),
                None,
            )
            observation_error = pr_ops.refresh_head_observation(
                config, record, target_pr, str(result["head_sha"])
            )
            if observation_error:
                result["head_observation_error"] = observation_error
        except (OSError, ValueError) as exc:
            result["head_observation_error"] = str(exc)
    if use_json:
        output._json_output(result)
    elif result.get("success"):
        output.ok(
            f"Recorded PR for {worktree_id}: "
            f"#{result.get('number')} ({result.get('state')}) {result.get('url')}"
        )
        if result.get("head_observation_error"):
            output.warn(
                "PR metadata was recorded, but authoritative head observation "
                f"failed: {result['head_observation_error']}"
            )
    else:
        output.err(result.get("error", "set-pr failed."))
    return 0 if result.get("success") else 1


def cmd_pr_abandon(args: argparse.Namespace) -> int:
    """Close a tracked PR without merging -- the ``pr-abandon`` CLI verb.

    See :func:`pr_ops.abandon_pr` for the full two-step confirmation
    contract: the first (no ``--confirm``) call always refuses with a
    stern, alternatives-first warning and mutates nothing; only a second,
    explicit ``--confirm`` call (after real operator sign-off) proceeds.
    """
    core = _core()
    use_json = getattr(args, "json", False)
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
    except Exception as e:
        if use_json:
            return output._json_error(str(e))
        raise
    worktree_id = core._infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        msg = "Could not determine worktree ID. Pass it explicitly or run from inside a worktree."
        return output._json_error(msg) if use_json else (output.err(msg) or 1)
    worktree_id = core._resolve_worktree_id(worktree_id)

    result = pr_ops.abandon_pr(
        worktree_id,
        config,
        target_repo=getattr(args, "repo", None),
        pr_number=getattr(args, "pr", None),
        reason=getattr(args, "reason", None) or "",
        confirm=getattr(args, "confirm", False),
    )
    if use_json:
        output._json_output(result)
    elif result.get("success"):
        n = result.get("number")
        repo = result.get("repo")
        verb = "was already closed" if result.get("already_closed") else "closed (not merged)"
        output.ok(
            f"PR #{n} ({repo}) {verb}; its claim is now settled as abandoned. "
            + (f"Warning: {result['warning']}" if result.get("warning") else "")
        )
    elif result.get("needs_confirm"):
        # The stern warning IS the primary output here, not an incidental
        # error -- print it directly rather than through output.err's
        # single-line framing.
        print(result.get("error", ""), file=sys.stderr)
    else:
        output.err(result.get("error", "pr-abandon failed."))
    return 0 if result.get("success") else 1


def cmd_pr_ready(args: argparse.Namespace) -> int:
    core = _core()
    use_json = getattr(args, "json", False)
    if use_json:
        ctx = output.stdout_to_stderr()
        ctx.__enter__()
    else:
        ctx = None  # type: ignore[assignment]

    try:
        try:
            config = cfg.load_config(Path(args.config) if args.config else None)
        except Exception as e:
            if use_json:
                return output._json_error(str(e))
            raise
        worktree_id = core._infer_worktree_id(args.worktree_id, config)
        if not worktree_id:
            msg = (
                "Could not determine worktree ID. Pass it explicitly "
                "or run from inside a worktree."
            )
            if use_json:
                return output._json_error(msg)
            output.err(msg)
            return 1
        worktree_id = core._resolve_worktree_id(worktree_id)

        result = pr_ops.pr_ready(
            worktree_id,
            config,
            target_repo=getattr(args, "repo", None),
            pr_number=getattr(args, "pr", None),
        )
        if use_json:
            output._json_output(result)
        elif result.get("success"):
            n = result.get("number")
            repo = result.get("repo")
            url = result.get("url")
            if result.get("transition") == "release-legacy-hold":
                output.ok(
                    f"Removed legacy hold label from PR #{n} ({repo}): {url}. "
                    f"PR is ready for review (it does not grant merge consent -- "
                    f"use pr-merge for that)."
                )
            else:
                output.ok(
                    f"Moved PR #{n} out of draft ({repo}): {url}. "
                    f"It is now ready for review (pr-ready does not grant merge "
                    f"consent -- use pr-merge for that)."
                )
        else:
            output.err(result.get("error", "pr-ready failed."))
        return 0 if result.get("success") else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def cmd_pr_status(args: argparse.Namespace) -> int:
    core = _core()
    use_json = getattr(args, "json", False)
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
    except Exception as e:
        if use_json:
            return output._json_error(str(e))
        raise
    worktree_id = core._infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        msg = "Could not determine worktree ID. Pass it explicitly or run from inside a worktree."
        return output._json_error(msg) if use_json else (output.err(msg) or 1)
    worktree_id = core._resolve_worktree_id(worktree_id)

    repo_cfg = config.default_repo
    configured_flow = pr_config._pr_flow_profile(repo_cfg)
    actor_flow = pr_config.ActorPRFlow(
        pr_config=repo_cfg.pr,
        configured_flow=configured_flow,
        flow=configured_flow,
    )
    live_requested = not getattr(args, "no_live", False)
    if live_requested:
        try:
            record = tracking.load_record(cfg.tracking_dir() / f"{worktree_id}.yaml")
            active = record.active_pr()
            target_repo = (
                (active.repo if active else "")
                or record.repo
                or ""
            )
        except Exception:
            target_repo = ""
        if target_repo:
            actor_flow = pr_config.resolve_actor_pr_flow(repo_cfg, target_repo)

    result = pr_ops.pr_status(
        worktree_id,
        all_prs=getattr(args, "all", False),
        live=live_requested,
        config=config,
        prcfg=actor_flow.pr_config,
    )
    flow = actor_flow.flow
    result["flow"] = {
        "profile": flow.profile,
        "configured_profile": actor_flow.configured_flow.profile,
        "resolution": actor_flow.resolution,
        "viewer_permission": actor_flow.viewer_permission,
        "requires_pr": flow.requires_pr,
        "merge_mode": flow.merge_mode,
        "applicable_verbs": list(flow.applicable_verbs),
        "summary": flow.summary,
    }
    from . import pr_contract as pc

    live_block = result.get("live")
    live = live_block if isinstance(live_block, dict) else {}
    if result.get("state") == "merged" or live.get("merge_state") == "merged":
        state = pc.PR_STATE_MERGED
    elif live.get("conflict"):
        state = pc.PR_STATE_CONFLICT
    elif live.get("verdict") == "approved":
        state = pc.PR_STATE_APPROVED
    elif live.get("verdict") in ("changes_requested", "change_requested"):
        state = pc.PR_STATE_CHANGES_REQUESTED
    elif result.get("has_pr"):
        state = pc.PR_STATE_AWAITING_REVIEW
    else:
        state = ""
    reminder = context_cli._pr_reminder_for(
        config,
        "pr-status",
        state=state,
        ok=not result.get("error"),
        reason=(live.get("self_merge_note") or "") if isinstance(live_block, dict) else "",
        flow=flow,
    )
    if reminder is not None:
        result["reminder"] = reminder.as_dict()
    want_threads = getattr(args, "threads", False) or getattr(args, "resolve_threads", False)
    if want_threads and result.get("has_pr"):
        result["thread_report"] = pr_ops.pr_threads(
            worktree_id,
            resolve=getattr(args, "resolve_threads", False),
            config=config,
        )
    if use_json:
        output._json_output(result)
        return 0 if result.get("has_pr") or "error" not in result else 1
    if result.get("error"):
        output.err(result["error"])
        return 1
    flow_detail = flow.profile
    if actor_flow.resolution != "configured":
        permission = actor_flow.viewer_permission or "unknown"
        flow_detail += (
            f" (configured: {actor_flow.configured_flow.profile}; "
            f"permission: {permission}; {actor_flow.resolution})"
        )
    print(f"  flow:     {flow_detail} -- {flow.summary}")
    if reminder is not None:
        print(reminder.text(), file=sys.stderr)
    if not result.get("has_pr"):
        print(f"{worktree_id}: no PR recorded (direct-push or not yet created).")
        return 0
    count = result.get("pr_count", 1)
    print(f"PR for {worktree_id} (active of {count}):")
    print(f"  state:    {result.get('state')}")
    print(f"  branch:   {result.get('branch')}")
    print(f"  number:   {result.get('number')}")
    print(f"  url:      {result.get('url')}")
    print(f"  provider: {result.get('provider')}")
    if result.get("repo"):
        print(f"  repo:     {result.get('repo')}")
    if isinstance(live_block, dict):
        verdict = live.get("verdict") or "(none)"
        print("  live:")
        print(f"    verdict:     {verdict}")
        print(f"    merge state: {live.get('merge_state')}")
        if live.get("conflict"):
            print("    conflict:    yes (needs rebase)")
        if live.get("held"):
            print(f"    held by:     {', '.join(live['held'])}")
        if live.get("wip"):
            print("    wip:         yes")
        consent = (
            "present"
            if live.get("consent_present")
            else ("eligible" if live.get("eligible") else "not yet")
        )
        print(f"    consent:     {consent}")
        if live.get("self_merge_note"):
            output.ok(f"    {live['self_merge_note']}")
    if getattr(args, "all", False) and result.get("prs"):
        print(f"  all PRs ({count}):")
        for pr in result["prs"]:
            num = f"#{pr['number']}" if pr.get("number") else "(unnumbered)"
            print(f"    - {num} [{pr.get('state')}] {pr.get('branch')}")
    if result.get("pull_forward_recommended"):
        print()
        output.warn("Pull-forward recommended (active PR merged):")
        print(f"  {result.get('next_action')}")

    threads = result.get("thread_report")
    if isinstance(threads, dict):
        if not threads.get("supported", True):
            output.warn(f"  threads:  unavailable ({threads.get('reason', '')})")
        else:
            active = threads.get("active_count", 0)
            print(f"  threads:  {len(threads.get('threads', []))} ({active} active)")
            for thread in threads.get("threads", []):
                if not thread.get("active"):
                    continue
                loc = f" {thread['file_path']}" if thread.get("file_path") else ""
                print(f"    - #{thread.get('id')} [{thread.get('status')}]{loc}")
                for comment in thread.get("comments", []):
                    body = (comment.get("content") or "").strip().replace("\n", " ")
                    print(f"        {comment.get('author')}: {body[:200]}")
            if threads.get("resolved"):
                output.ok("  resolved active comment threads.")
            elif threads.get("resolve_error"):
                output.warn(f"  resolve failed: {threads.get('resolve_error')}")
    return 0


def cmd_pr_nudge(args: argparse.Namespace) -> int:
    core = _core()
    from . import pr_nudge_ops

    use_json = getattr(args, "json", False)
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
    except Exception as e:
        if use_json:
            return output._json_error(str(e))
        raise
    worktree_id = core._infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        msg = "Could not determine worktree ID. Pass it explicitly or run from inside a worktree."
        return output._json_error(msg) if use_json else (output.err(msg) or 1)
    worktree_id = core._resolve_worktree_id(worktree_id)

    result = pr_nudge_ops.pr_nudge(worktree_id, config=config)
    if use_json:
        output._json_output(result)
        return 0 if "error" not in result else 1
    if result.get("error"):
        output.err(result["error"])
        return 1
    if not result.get("has_pr"):
        print(result.get("detail") or f"{worktree_id}: no PR tracked (nothing to nudge).")
        return 0
    if not result.get("supported"):
        output.warn(result.get("detail") or "No automated reviewer bound (nothing to nudge).")
        return 0
    if result.get("requested"):
        output.ok(f"Requested a review from {result.get('reviewer')} on "
                   f"#{result.get('number')} ({result.get('repo')}).")
        if result.get("detail"):
            print(f"  {result['detail']}")
    else:
        output.err(result.get("detail") or "Review request failed.")
        return 1
    return 0


def cmd_pr_complete(args: argparse.Namespace) -> int:
    core = _core()
    from . import pr_complete

    use_json = getattr(args, "json", False)
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
    except Exception as e:
        if use_json:
            return output._json_error(str(e))
        raise
    worktree_id = core._infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        msg = "Could not determine worktree ID. Pass it explicitly or run from inside a worktree."
        return output._json_error(msg) if use_json else (output.err(msg) or 1)
    worktree_id = core._resolve_worktree_id(worktree_id)

    result = pr_complete.complete_worktree(
        worktree_id,
        config,
        dry_run=getattr(args, "dry_run", False),
    )
    if use_json:
        output._json_output(result)
        return 0 if result.get("success") else 1
    if result.get("success"):
        output.ok(result.get("message", f"pr-complete: {result.get('action')}"))
        if result.get("action") == "reset-past-squash" and result.get("backup_ref"):
            print(
                f"  recover the pre-complete state with: git reset --hard {result['backup_ref']}"
            )
    else:
        output.err(result.get("error", "pr-complete failed."))
    return 0 if result.get("success") else 1


def cmd_pr_diff(args: argparse.Namespace) -> int:
    core = _core()
    from . import pr_reviewer_ops

    use_json = getattr(args, "json", False)
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
    except Exception as e:
        if use_json:
            return output._json_error(str(e))
        raise
    worktree_id = core._infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        msg = "Could not determine worktree ID. Pass it explicitly or run from inside a worktree."
        return output._json_error(msg) if use_json else (output.err(msg) or 1)
    worktree_id = core._resolve_worktree_id(worktree_id)

    result = pr_reviewer_ops.pr_diff(worktree_id, config=config)
    if use_json:
        output._json_output(result)
        return 0 if "error" not in result else 1
    if result.get("error"):
        output.err(result["error"])
        return 1
    if not result.get("has_pr"):
        print(result.get("detail") or f"{worktree_id}: no PR tracked (nothing to diff).")
        return 0
    if not result.get("supported"):
        output.warn("This provider does not support reading a unified diff.")
        return 1
    print(result.get("diff", ""))
    return 0


def cmd_pr_comment(args: argparse.Namespace) -> int:
    core = _core()
    from . import pr_reviewer_ops

    use_json = getattr(args, "json", False)
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
    except Exception as e:
        if use_json:
            return output._json_error(str(e))
        raise
    worktree_id = core._infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        msg = "Could not determine worktree ID. Pass it explicitly or run from inside a worktree."
        return output._json_error(msg) if use_json else (output.err(msg) or 1)
    worktree_id = core._resolve_worktree_id(worktree_id)

    result = pr_reviewer_ops.pr_comment(worktree_id, args.body, config=config)
    if use_json:
        output._json_output(result)
        return 0 if "error" not in result else 1
    if result.get("error"):
        output.err(result["error"])
        return 1
    if not result.get("has_pr"):
        print(result.get("detail") or f"{worktree_id}: no PR tracked (nothing to comment on).")
        return 0
    output.ok(f"Posted a comment on #{result.get('number')} ({result.get('repo')}).")
    return 0


def cmd_pr_review(args: argparse.Namespace) -> int:
    core = _core()
    from . import pr_reviewer_ops

    use_json = getattr(args, "json", False)
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
    except Exception as e:
        if use_json:
            return output._json_error(str(e))
        raise
    worktree_id = core._infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        msg = "Could not determine worktree ID. Pass it explicitly or run from inside a worktree."
        return output._json_error(msg) if use_json else (output.err(msg) or 1)
    worktree_id = core._resolve_worktree_id(worktree_id)

    result = pr_reviewer_ops.pr_review(
        worktree_id, event=args.event, body=getattr(args, "body", ""), config=config
    )
    if use_json:
        output._json_output(result)
        return 0 if "error" not in result else 1
    if result.get("error"):
        output.err(result["error"])
        return 1
    if not result.get("has_pr"):
        print(result.get("detail") or f"{worktree_id}: no PR tracked (nothing to review).")
        return 0
    output.ok(
        f"Submitted a {result.get('event')} review on #{result.get('number')} "
        f"({result.get('repo')})."
    )
    return 0
