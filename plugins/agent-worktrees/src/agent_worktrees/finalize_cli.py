"""Finalize / push / create-pr CLI surface extracted from ``__main__``."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import codename_tracking, config as cfg, finalize as fin, git_ops, output, pr_ops, tracking
from . import context_cli


def _core():
    from . import __main__ as core

    return core


def add_parsers(sub) -> None:
    p = sub.add_parser("post-exit", help="Post-exit worktree checks (idempotent)")
    p.add_argument("worktree_id", nargs="?", default=None)

    p = sub.add_parser(
        "finalize",
        help="Validate the branch's content is on upstream; prune the worktree only when idle",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument(
        "--worktree-id",
        dest="worktree_id_flag",
        default=None,
        help="Worktree ID to finalize (explicit automation form)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--abandon",
        action="store_true",
        help="Finalize past the obligation gate even when the worktree "
        "still owns unsettled outbound resources; requires an "
        "operator-directed --handoff-to recipient/flow.",
    )
    p.add_argument(
        "--handoff-to",
        default=None,
        help="With --abandon, affirmative recipient or cleanup flow "
        "that accepts responsibility for every re-homed resource",
    )
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only)")
    p.add_argument("--config", default=None)

    p = sub.add_parser("push-changes", help="Push worktree changes to remote default branch")
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--title", default=None, help="Set worktree title")
    p.add_argument(
        "--title-only",
        action="store_true",
        help="Set title without pushing (worktree stays active)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--allow-unsquashed",
        action="store_true",
        help="If the pre-squash step fails, push the individual "
        "commits instead of aborting. Off by default -- a "
        "squash failure must never silently push every commit "
        "to the shared default branch (issue #783).",
    )
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only)")
    p.add_argument("--config", default=None)

    p = sub.add_parser(
        "create-pr",
        aliases=["pr-create"],
        help="Squash worktree commits, create + push a feature branch for a PR "
        "(pr-create is the pr-* family alias)",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--title", default=None, help="Title for the squashed commit / PR slug")
    p.add_argument("--branch", default=None, help="Override the generated feature branch name")
    p.add_argument(
        "--topic",
        default=None,
        help="Optional mini-task token folded into the generated default branch name",
    )
    p.add_argument(
        "--repo",
        default=None,
        help="Target repo 'owner/name' for the PR (default: the worktree repo)",
    )
    p.add_argument(
        "--new",
        action="store_true",
        help="Force a brand-new PR (fresh branch) even if a live PR is open",
    )
    p.add_argument(
        "--body",
        default=None,
        help="PR body text (the repo may append source attribution "
        "when pr.source_attribution is enabled)",
    )
    p.add_argument(
        "--body-file", default=None, dest="body_file", help="Read the PR body from a file"
    )
    p.add_argument(
        "--no-open",
        action="store_true",
        dest="no_open",
        help="Push the branch only; do not auto-open the PR via the provider",
    )
    p.add_argument(
        "--draft",
        action="store_true",
        help="Open the PR as a native DRAFT (not yet ready for "
        "review). 'pr-ready' moves it out of draft. Lets you "
        "iterate on the open PR before requesting review.",
    )
    p.add_argument(
        "--hold",
        action="store_true",
        dest="hold",
        help="Deprecated alias for --draft (the old do-not-merge "
        "label hold is retired in favour of native draft state).",
    )
    p.add_argument(
        "--no-attribution",
        action="store_true",
        dest="no_attribution",
        help="Omit source-worktree attribution even when the repo enables pr.source_attribution",
    )
    p.add_argument(
        "--confirm-fork",
        action="store_true",
        dest="confirm_fork",
        help="Confirm setting up + publishing through a personal fork, when "
        "this repo's resolved role requires it (pr.fork/pr.roles). Without "
        "this flag, create-pr stops and asks for confirmation instead of "
        "forking/pushing anywhere.",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only)")
    p.add_argument("--config", default=None)

    p = sub.add_parser(
        "attribution-audit",
        help="Flag this repo's pr.head_pattern for the branch-name leak class "
        "(config-only; does not touch git or a provider)",
    )
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    p = sub.add_parser(
        "mark-complete",
        help=argparse.SUPPRESS,
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--title", default=None)
    p.add_argument("--title-only", action="store_true")


def _sweep_orphans_on_exit() -> None:
    core = _core()
    try:
        payload = core.reap_orphan_mux_sessions()
        reaped = payload.get("reaped") or []
        if reaped:
            output.ok(f"Reaped {len(reaped)} idle orphan mux session(s): {', '.join(reaped)}")
    except Exception:
        pass
    try:
        core._sweep_managed_on_exit()
    except Exception:
        pass
    try:
        core._sweep_launcher_shells_on_exit()
    except Exception:
        pass
    try:
        core._sweep_finished_sessions_on_cadence()
    except Exception:
        pass


def _invoke_post_exit_sweep() -> None:
    """Honor ``__main__`` monkeypatch seams without recursing through our re-export."""
    core = _core()
    sweep = getattr(core, "_sweep_orphans_on_exit", None)
    if sweep is _sweep_orphans_on_exit or sweep is None:
        _sweep_orphans_on_exit()
        return
    sweep()


def cmd_post_exit(args: argparse.Namespace) -> int:
    core = _core()
    config = cfg.load_config()
    worktree_id = core._infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        output.err(
            "Could not determine worktree ID. Pass it explicitly or run from inside a worktree."
        )
        return 1
    worktree_id = core._resolve_worktree_id(worktree_id)

    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        output.warn(f"No tracking record for {worktree_id} -- skipping post-exit.")
        _invoke_post_exit_sweep()
        return 0

    try:
        record = tracking.load_record(yaml_path)
    except Exception as e:
        output.err(f"Failed to load record {worktree_id}: {e}")
        return 1

    if record.status == "finalized":
        output.ok(f"Worktree {worktree_id} already finalized.")
        rc = 0
    else:
        rc = _post_exit_gate(record, config)

    _invoke_post_exit_sweep()
    return rc


def _post_exit_gate(record: tracking.WorktreeRecord, config: cfg.Config) -> int:
    worktree_id = record.worktree_id

    if record.status in ("complete", "pushed"):
        print(f"Session {worktree_id} ready for finalization -- validating...")
        success = fin.validate_and_finalize(worktree_id, config)
        if success:
            return 0
        output.err(
            f"Finalization failed for {worktree_id}. Run 'agent-worktrees finalize' to retry."
        )
        return 1

    if record.status == "orphaned":
        output.warn(
            f"Session {worktree_id} is orphaned (previous push failed). "
            f"Run 'agent-worktrees push-changes' to retry pushing, "
            f"then 'agent-worktrees finalize' to clean up."
        )
        return 0

    print(f"Session {worktree_id} is still active (not pushed/completed). Skipping finalization.")
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
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
                return core._json_error(str(e))
            raise
        positional_id = getattr(args, "worktree_id", None)
        flagged_id = getattr(args, "worktree_id_flag", None)
        if positional_id and flagged_id and positional_id != flagged_id:
            msg = "Conflicting worktree IDs: positional worktree-id and --worktree-id must match."
            if use_json:
                return core._json_error(msg, 2)
            output.err(msg)
            return 2
        worktree_id = core._infer_worktree_id(flagged_id or positional_id, config)
        if not worktree_id:
            msg = (
                "Could not determine worktree ID. Pass it explicitly "
                "or run from inside a worktree."
            )
            if use_json:
                return core._json_error(msg)
            output.err(msg)
            return 1
        worktree_id = core._resolve_worktree_id(worktree_id)
        abandon = getattr(args, "abandon", False)
        handoff_to = (getattr(args, "handoff_to", None) or "").strip()
        if abandon and not handoff_to:
            msg = (
                "--abandon requires --handoff-to <recipient-or-flow>. "
                "The creating agent owns cleanup unless the operator explicitly "
                "directed an affirmative handoff."
            )
            if use_json:
                return core._json_error(msg, 2)
            output.err(msg)
            return 2
        if handoff_to and not abandon:
            msg = "--handoff-to is valid only with --abandon"
            if use_json:
                return core._json_error(msg, 2)
            output.err(msg)
            return 2
        success = fin.validate_and_finalize(
            worktree_id,
            config,
            dry_run=args.dry_run,
            abandon=abandon,
            handoff_to=handoff_to or None,
        )

        if use_json:
            yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
            final_status = "finalized"
            if yaml_path.exists():
                try:
                    rec = tracking.load_record(yaml_path)
                    final_status = rec.status
                except Exception:
                    pass
            core._json_output(
                {
                    "worktree_id": worktree_id,
                    "success": success,
                    "status": final_status,
                }
            )

        return 0 if success else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def cmd_push_changes(args: argparse.Namespace) -> int:
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
                return core._json_error(str(e))
            raise
        worktree_id = core._infer_worktree_id(args.worktree_id, config)
        if not worktree_id:
            msg = (
                "Could not determine worktree ID. Pass it explicitly "
                "or run from inside a worktree."
            )
            if use_json:
                return core._json_error(msg)
            output.err(msg)
            return 1
        worktree_id = core._resolve_worktree_id(worktree_id)

        if getattr(args, "title_only", False):
            yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
            if not yaml_path.exists():
                output.err(f"Tracking file not found for {worktree_id}")
                return 1
            if args.title:
                with tracking._RecordLock(yaml_path):
                    record = tracking.load_record(yaml_path)
                    record.title = tracking.cap_title(args.title)
                    record.title_asserted = record.title is not None
                    tracking.save_record(record)
                print(f"[OK] Worktree {worktree_id} title updated: {record.title}")
            else:
                output.err("--title-only requires --title")
                return 1
            return 0

        success = fin.push_changes(
            worktree_id,
            config,
            title=args.title,
            dry_run=args.dry_run,
            allow_unsquashed=getattr(args, "allow_unsquashed", False),
        )

        reminder = context_cli._pr_reminder_for(
            config,
            "push-changes",
            ok=bool(success),
        )
        if use_json:
            yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
            final_status = "pushed"
            if yaml_path.exists():
                try:
                    rec = tracking.load_record(yaml_path)
                    final_status = rec.status
                except Exception:
                    pass
            out = {
                "worktree_id": worktree_id,
                "success": success,
                "status": final_status,
            }
            if reminder is not None:
                out["reminder"] = reminder.as_dict()
            core._json_output(out)
        elif reminder is not None:
            print(reminder.text(), file=sys.stderr)

        return 0 if success else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def cmd_create_pr(args: argparse.Namespace) -> int:
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
                return core._json_error(str(e))
            raise
        worktree_id = core._infer_worktree_id(args.worktree_id, config)
        if not worktree_id:
            msg = (
                "Could not determine worktree ID. Pass it explicitly "
                "or run from inside a worktree."
            )
            if use_json:
                return core._json_error(msg)
            output.err(msg)
            return 1
        worktree_id = core._resolve_worktree_id(worktree_id)

        body = getattr(args, "body", None)
        body_file = getattr(args, "body_file", None)
        if body_file:
            try:
                body = Path(body_file).read_text(encoding="utf-8")
            except OSError as e:
                msg = f"Could not read --body-file '{body_file}': {e}"
                return core._json_error(msg) if use_json else (output.err(msg) or 1)

        try:
            result = pr_ops.create_pr(
                worktree_id,
                config,
                title=args.title,
                branch=args.branch,
                topic=getattr(args, "topic", None),
                target_repo=getattr(args, "repo", None),
                new=getattr(args, "new", False),
                body=body,
                open_pr=(False if getattr(args, "no_open", False) else None),
                hold=getattr(args, "hold", False),
                draft=getattr(args, "draft", False),
                attribution=(False if getattr(args, "no_attribution", False) else None),
                dry_run=args.dry_run,
                confirm_fork=getattr(args, "confirm_fork", False),
            )
        except codename_tracking.CodenameAttributionPolicyError as e:
            msg = str(e)
            return core._json_error(msg) if use_json else (output.err(msg) or 1)

        reminder = context_cli._pr_reminder_for(
            config,
            "create-pr",
            state=("created" if result.get("success") else ""),
            ok=bool(result.get("success")),
            reason=("" if result.get("success") else result.get("error", "")),
        )
        if reminder is not None:
            result["reminder"] = reminder.as_dict()
        if result.get("pr_opened") and result.get("url"):
            result["pr"] = {
                "ref": result.get("url"),
                "url": result.get("url"),
                "number": result.get("number"),
                "provider": result.get("provider"),
            }
        if use_json:
            core._json_output(result)
        elif result.get("success"):
            branch = result.get("branch", "")
            remote = result.get("remote", "")
            provider = result.get("provider", "")
            output.ok(f"Feature branch '{branch}' pushed to {remote}.")
            if result.get("topic_note"):
                output.warn(result["topic_note"])
            print(
                f"  base: {result.get('base_sha', '')[:10]}  "
                f"head: {result.get('head_sha', '')[:10]}"
            )
            if result.get("pr_opened"):
                output.ok(
                    f"Opened PR #{result.get('number')} via '{provider}': {result.get('url')}"
                )
                if result.get("draft"):
                    output.warn(
                        f"PR #{result.get('number')} opened as a DRAFT "
                        f"(not yet ready for review). Run "
                        f"'agent-worktrees pr-ready' to move it out of draft."
                    )
                if result.get("pr_label_error"):
                    output.warn(
                        f"PR opened, but a label did not apply: "
                        f"{result.get('pr_label_error')}. Re-apply the label(s) "
                        f"via the '{provider}' provider."
                    )
                if result.get("self_merge_note"):
                    output.ok(result["self_merge_note"])
            elif result.get("pr_open_error"):
                output.warn(f"Branch pushed, but auto-open failed: {result.get('pr_open_error')}")
                print(
                    f"Open the PR via the '{provider}' provider, then record it:\n"
                    f"  agent-worktrees set-pr {worktree_id} --url <URL> --number <N>"
                )
            else:
                print(
                    f"Next: delegate PR creation to the '{provider}' provider, "
                    f"then record it with:\n"
                    f"  agent-worktrees set-pr {worktree_id} --url <URL> --number <N>"
                )
        elif result.get("needs_confirmation"):
            output.warn(result.get("message", "Confirmation needed before continuing."))
        else:
            output.err(result.get("error", "create-pr failed."))

        if not use_json and reminder is not None:
            print(reminder.text(), file=sys.stderr)
        return 0 if result.get("success") else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def cmd_attribution_audit(args: argparse.Namespace) -> int:
    core = _core()
    use_json = getattr(args, "json", False)
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
    except Exception as e:
        return core._json_error(str(e)) if use_json else (output.err(str(e)) or 1)
    findings = pr_ops.audit_attribution_risk(config)
    if use_json:
        core._json_output({"success": True, "findings": findings})
        return 0
    if not findings:
        output.ok("No branch-name leak-class risk found in this repo's PR config.")
        return 0
    for finding in findings:
        output.warn(finding)
    return 1


def cmd_mark_complete(args: argparse.Namespace) -> int:
    core = _core()
    config = cfg.load_config()
    worktree_id = core._infer_worktree_id(args.worktree_id, config)

    if not worktree_id:
        output.err(
            "Could not determine worktree ID. Pass it explicitly or run from inside a worktree."
        )
        return 1
    worktree_id = core._resolve_worktree_id(worktree_id)

    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"

    if not yaml_path.exists():
        output.warn(f"Tracking file not found at {yaml_path}")
        print("Creating minimal tracking file...")
        capped = tracking.cap_title(args.title)
        record = tracking.WorktreeRecord(
            worktree_id=worktree_id,
            branch=git_ops.get_current_branch("."),
            worktree_path=str(Path.cwd()),
            repo=cfg.project_name(),
            machine="",
            platform="",
            started_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            last_resumed_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            resume_count=0,
            title=capped,
            title_asserted=capped is not None,
            status="active" if args.title_only else "complete",
            completed_at=None if args.title_only else datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        )
        tracking.save_record(record, yaml_path)
    else:
        if (not args.title_only) or args.title:
            with tracking._RecordLock(yaml_path):
                record = tracking.load_record(yaml_path)
                if args.title:
                    record.title = tracking.cap_title(args.title)
                    record.title_asserted = record.title is not None
                if not args.title_only:
                    tracking.update_status(record, "complete", save=False)
                tracking.save_record(record)

    if args.title_only:
        print(f"[OK] Worktree {worktree_id} title updated: {args.title}")
        return 0

    msg = f"[OK] Worktree {worktree_id} marked complete (status flag only)."
    if args.title:
        msg += f" Title: {args.title}"
    print(msg)
    print(
        "NOTE: This only sets the tracking flag. Content has NOT been pushed. "
        "For normal sign-off, use 'agent-worktrees push-changes' + "
        "'agent-worktrees finalize' instead."
    )
    return 0
