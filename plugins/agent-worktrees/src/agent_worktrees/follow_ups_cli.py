"""Follow-up obligation CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import activity, output, tracking
from . import config as cfg


def _core():
    from . import __main__ as core

    return core


def _json_error(*args, **kwargs):
    return _core()._json_error(*args, **kwargs)


def _json_output(*args, **kwargs):
    return _core()._json_output(*args, **kwargs)


def _infer_worktree_id(*args, **kwargs):
    return _core()._infer_worktree_id(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "follow-ups",
        help="List/add/resolve/dismiss itemized follow-up obligations on a "
        "worktree. Defaults to the current worktree; pass an id for "
        "another. An open (or pending-transfer) item counts as an open "
        "obligation the same way the legacy --follow-up flag did.",
    )
    p.add_argument(
        "target",
        nargs="*",
        default=None,
        help="[worktree_id] to list, OR 'add <summary>' to journal a new "
        "open follow-up, OR 'resolve <id>' to mark one done, OR "
        "'dismiss <id>' to mark one explicitly not requiring action",
    )
    p.add_argument(
        "--ref",
        action="append",
        default=None,
        dest="follow_up_refs",
        metavar="KIND:VALUE",
        help="with add: a typed objective reference (kind: resource-claim | "
        "dispatch-task | issue | pull-request | file | effort | other), "
        "repeatable",
    )
    p.add_argument(
        "--result-ref",
        default=None,
        dest="follow_up_result_ref",
        help="with resolve: an optional reference to the result",
    )
    p.add_argument(
        "--reason",
        default="",
        dest="follow_up_reason",
        help="with dismiss: required explanation for why no action is needed",
    )
    p.add_argument(
        "--worktree",
        default=None,
        dest="follow_up_worktree",
        help="the owner worktree (default: current)",
    )
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only)")


def _parse_follow_up_refs(raw: list[str] | None) -> list[tracking.FollowUpRef]:
    """Parse repeatable ``--ref kind:value`` CLI args into ``FollowUpRef``s."""
    valid_kinds = {"resource-claim", "dispatch-task", "issue", "pull-request", "file", "effort", "other"}
    refs: list[tracking.FollowUpRef] = []
    for item in raw or []:
        kind, sep, value = item.partition(":")
        if not sep:
            refs.append(tracking.FollowUpRef(kind="other", ref=item))
            continue
        kind = kind.strip() if kind.strip() in valid_kinds else "other"
        refs.append(tracking.FollowUpRef(kind=kind, ref=value.strip()))
    return refs


def _follow_up_to_json(fu: tracking.FollowUpRecord) -> dict:
    return {
        "id": fu.id,
        "summary": fu.summary,
        "state": fu.state,
        "revision": fu.revision,
        "created_at": fu.created_at,
        "updated_at": fu.updated_at,
        "refs": [{"kind": r.kind, "ref": r.ref} for r in fu.refs],
        **({"result_ref": fu.result_ref} if fu.result_ref else {}),
        **({"reason": fu.reason} if fu.reason else {}),
    }


def cmd_follow_ups(args: argparse.Namespace) -> int:
    """Dispatch the follow-ups verb."""
    target = list(getattr(args, "target", None) or [])
    if target and target[0] == "add":
        if len(target) < 2:
            msg = "follow-ups add: usage 'add <summary>'"
            if args.json:
                return _json_error(msg, 2)
            output.err(msg)
            return 2
        return _follow_ups_add(args, " ".join(target[1:]))
    if target and target[0] == "resolve":
        if len(target) < 2:
            msg = "follow-ups resolve: missing <id>"
            if args.json:
                return _json_error(msg, 2)
            output.err(msg)
            return 2
        return _follow_ups_resolve(args, target[1])
    if target and target[0] == "dismiss":
        if len(target) < 2:
            msg = "follow-ups dismiss: missing <id>"
            if args.json:
                return _json_error(msg, 2)
            output.err(msg)
            return 2
        return _follow_ups_dismiss(args, target[1])
    worktree_id = target[0] if target else None
    return _follow_ups_show(args, worktree_id)


def _follow_ups_record_path(args: argparse.Namespace, config: cfg.Config) -> tuple[str, Path]:
    wt_id = _infer_worktree_id(getattr(args, "follow_up_worktree", None), config)
    return wt_id, cfg.tracking_dir() / f"{wt_id}.yaml"


def _follow_ups_show(args: argparse.Namespace, worktree_id: str | None) -> int:
    config = cfg.load_config()
    wt_id = _infer_worktree_id(worktree_id, config)
    rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return _json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    rec = tracking.load_record(rec_path)
    items = [_follow_up_to_json(fu) for fu in rec.follow_ups]
    open_count = tracking.effective_open_follow_up_count(rec)
    if args.json:
        _json_output(
            {
                "worktree_id": wt_id,
                "follow_ups": items,
                "open_count": open_count,
                "legacy_follow_up": rec.follow_up,
            }
        )
        return 0
    print(f"Follow-ups for {wt_id} -- {open_count} open")
    if not items:
        if rec.follow_up:
            print("  (legacy boolean follow_up=true, no itemized entries)")
        else:
            print("  (none)")
        return 0
    for fu in items:
        refs = ", ".join(f"{r['kind']}:{r['ref']}" for r in fu["refs"])
        line = f"  [{fu['state']}] {fu['id']}: {fu['summary']}"
        if refs:
            line += f"  ({refs})"
        print(line)
    return 0


def _follow_ups_add(args: argparse.Namespace, summary: str) -> int:
    config = cfg.load_config()
    blocked = _core()._require_coordination_readiness(config, json_out=args.json)
    if blocked is not None:
        return blocked
    wt_id, rec_path = _follow_ups_record_path(args, config)
    if not rec_path.exists():
        if args.json:
            return _json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    refs = _parse_follow_up_refs(getattr(args, "follow_up_refs", None))
    with tracking._RecordLock(rec_path, require_sidecar=True):
        rec = tracking.load_record(rec_path)
        was_finalized = rec.status == "finalized"
        try:
            item = tracking.add_follow_up(rec, summary, refs=refs, save=False)
        except ValueError as exc:
            if args.json:
                return _json_error(str(exc))
            output.err(str(exc))
            return 1
        reopened = was_finalized and rec.status == "active"
        tracking.save_record(rec, rec_path)
    activity.log_event(
        "follow_up_added",
        worktree_id=wt_id,
        follow_up_id=item.id,
        summary=item.summary,
        refs=[f"{r.kind}:{r.ref}" for r in item.refs],
        reopened=reopened,
    )
    if args.json:
        _json_output({"worktree_id": wt_id, **_follow_up_to_json(item), "reopened": reopened})
        return 0
    print(f"added follow-up {item.id} on {wt_id}: {item.summary}")
    if reopened:
        print(f"  reopened {wt_id}: finalized -> active (new open follow-up)")
    return 0


def _follow_ups_resolve(args: argparse.Namespace, follow_up_id: str) -> int:
    config = cfg.load_config()
    wt_id, rec_path = _follow_ups_record_path(args, config)
    if not rec_path.exists():
        if args.json:
            return _json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    with tracking._RecordLock(rec_path, require_sidecar=True):
        rec = tracking.load_record(rec_path)
        item = tracking.resolve_follow_up(
            rec,
            follow_up_id,
            result_ref=getattr(args, "follow_up_result_ref", None),
            save=False,
        )
        if item is None:
            msg = f"follow-ups resolve: no such follow-up {follow_up_id!r} on {wt_id}"
            if args.json:
                return _json_error(msg)
            output.err(msg)
            return 1
        tracking.save_record(rec, rec_path)
    activity.log_event(
        "follow_up_resolved",
        worktree_id=wt_id,
        follow_up_id=item.id,
        result_ref=item.result_ref,
    )
    if args.json:
        _json_output({"worktree_id": wt_id, **_follow_up_to_json(item)})
        return 0
    print(f"resolved follow-up {item.id} on {wt_id}")
    return 0


def _follow_ups_dismiss(args: argparse.Namespace, follow_up_id: str) -> int:
    config = cfg.load_config()
    reason = (getattr(args, "follow_up_reason", "") or "").strip()
    if not reason:
        msg = "follow-ups dismiss: requires --reason"
        if args.json:
            return _json_error(msg, 2)
        output.err(msg)
        return 2
    wt_id, rec_path = _follow_ups_record_path(args, config)
    if not rec_path.exists():
        if args.json:
            return _json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    with tracking._RecordLock(rec_path, require_sidecar=True):
        rec = tracking.load_record(rec_path)
        item = tracking.dismiss_follow_up(rec, follow_up_id, reason=reason, save=False)
        if item is None:
            msg = f"follow-ups dismiss: no such follow-up {follow_up_id!r} on {wt_id}"
            if args.json:
                return _json_error(msg)
            output.err(msg)
            return 1
        tracking.save_record(rec, rec_path)
    activity.log_event(
        "follow_up_dismissed",
        worktree_id=wt_id,
        follow_up_id=item.id,
        reason=reason,
    )
    if args.json:
        _json_output({"worktree_id": wt_id, **_follow_up_to_json(item)})
        return 0
    print(f"dismissed follow-up {item.id} on {wt_id}: {reason}")
    return 0
