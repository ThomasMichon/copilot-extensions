"""The existing effort transactions, shared by resident execution and direct fallback."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from . import effort_focus, tracking, tracking_write


def _result(
    record: tracking.WorktreeRecord,
    inspection: effort_focus.EffortInspection | None,
    **extra,
) -> dict:
    from .session_metadata_cli import _effort_focus_output

    return {"ok": True, "payload": _effort_focus_output(record, inspection), **extra}


def apply_bind(args: dict) -> dict:
    path = Path(args["yaml_path"])
    root = Path(args["repo_root"])
    try:
        ref = effort_focus.make_active_effort(args["path"], args["participant"], args["slice"])
        effort_focus.validate_binding(root, ref)
        with tracking._RecordLock(path.parent / ".effort-bindings.yaml", require_sidecar=True):
            records = tracking.list_records(path.parent)
            initial = tracking.load_record(path)
            conflict = effort_focus.duplicate_binding(
                records, initial.worktree_id, initial.repo, ref
            )
            if conflict:
                return {
                    "error": "that effort participant/slice is already bound to "
                    f"worktree {conflict}"
                }
            with tracking._RecordLock(path):
                record = tracking.load_record(path)
                inspection = effort_focus.validate_binding(root, ref)
                if record.active_effort == ref:
                    return _result(record, inspection, action="already bound", ref=ref.path)
                if record.active_effort is not None and not args["replace"]:
                    return {
                        "error": "an effort is already bound; pass --replace to record "
                        "an explicit replacement"
                    }
                replacing = record.active_effort is not None
                record.active_effort = ref
                record.effort_revision += 1
                tracking.set_disposition(
                    record,
                    summary=inspection.summary,
                    follow_up=True,
                    session_id=args.get("session_id"),
                    kind="effort-replace" if replacing else "effort-bind",
                    save=False,
                    tracking_path=path.parent,
                )
                tracking.save_record(record, path)
        return _result(
            record, inspection, action="replaced" if replacing else "bound", ref=ref.path
        )
    except ValueError as error:
        return {"error": str(error)}


def apply_release(args: dict) -> dict:
    path = Path(args["yaml_path"])
    root = None if args.get("repo_root") is None else Path(args["repo_root"])
    try:
        bound = effort_focus.make_active_effort(
            **{
                "path": args["bound"]["path"],
                "participant": args["bound"]["participant"],
                "slice_name": args["bound"]["slice"],
            }
        )
        completed = args["completed"]
        transfer = args["transfer"]
        if type(completed) is not bool or completed == bool(transfer):
            return {"error": "release requires exactly one of --completed or --transfer TARGET"}
        if transfer:
            transfer = effort_focus.normalize_label(transfer, "transfer target")
        with tracking._RecordLock(path):
            record = tracking.load_record(path)
            if record.active_effort != bound:
                return {"error": "effort focus changed while release was being prepared; retry"}
            if completed and (root is None or not effort_focus.completed_or_archived(root, bound)):
                return {
                    "error": "the bound effort changed or no longer satisfies the "
                    "completion gate; retry after resolving it"
                }
            record.active_effort = None
            record.effort_revision += 1
            slug = PurePosixPath(bound.path).parent.name
            summary = (
                f"Completed effort {slug}"
                if completed
                else f"Transferred effort {slug} to {transfer}"
            )
            tracking.set_disposition(
                record,
                summary=summary,
                follow_up=False,
                session_id=args.get("session_id"),
                kind="effort-release",
                save=False,
                tracking_path=path.parent,
            )
            tracking.save_record(record, path)
        return _result(record, None, summary=summary)
    except ValueError as error:
        return {"error": str(error)}


def dispatch(verb: str, args: dict) -> dict:
    from . import locks, status_monitor_runtime

    try:
        return tracking_write.dispatch(
            verb,
            args,
            read_lock_data=lambda: locks.read_lock(status_monitor_runtime._monitor_lock_path()),
            ensure_monitor=(
                status_monitor_runtime._ensure_status_monitor
                if status_monitor_runtime._status_monitor_enabled() else None
            ),
        )
    except tracking_write.ResidentRecoveryHeld as error:
        return {"error": str(error)}


tracking_write.register_verb("effort_bind", apply_bind)
tracking_write.register_verb("effort_release", apply_release)
