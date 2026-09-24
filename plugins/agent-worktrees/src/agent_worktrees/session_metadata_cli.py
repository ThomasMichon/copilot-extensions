"""Session metadata / history CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path, PurePosixPath

from . import claimant as claimant_mod
from . import config as cfg
from . import disposition_history, effort_focus, output, tracking
from . import state_root as state_root_mod
from . import status_updater_cli


def _core():
    from . import __main__ as core

    return core


def _activate_project_for_path(*args, **kwargs):
    return status_updater_cli._activate_project_for_path(*args, **kwargs)


def _infer_worktree_id(*args, **kwargs):
    return _core()._infer_worktree_id(*args, **kwargs)


def _json_error(*args, **kwargs):
    return _core()._json_error(*args, **kwargs)


def _json_output(*args, **kwargs):
    return _core()._json_output(*args, **kwargs)


def _resolve_worktree_id(*args, **kwargs):
    return _core()._resolve_worktree_id(*args, **kwargs)


def resolve_worktree_id_by_codename(*args, **kwargs):
    return _core().resolve_worktree_id_by_codename(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "session-lock",
        help="Write/remove a session-state lattice lock -- a provable-liveness "
        "marker beside Copilot's inuse lock, so the picker reads a "
        "bridge/mux session's liveness file-first",
    )
    p.add_argument("action", choices=["write", "remove"])
    p.add_argument(
        "--session", required=True, help="Copilot session id (the session-state dir name)"
    )
    p.add_argument(
        "--worktree",
        default=None,
        help="Worktree id this session is bound to (recorded in the "
        "lock for cwd-independent attribution)",
    )
    p.add_argument(
        "--pid",
        type=int,
        default=None,
        help="Owner process pid whose liveness the lock proves "
        "(e.g. the bridge-owned Copilot child); default: caller",
    )
    p.add_argument(
        "--kind", default="bridge", choices=["bridge"], help="Lattice layer (default: bridge)"
    )
    p.add_argument("--json", action="store_true", help="JSON output mode")

    p = sub.add_parser(
        "effort-focus",
        help="Bind, inspect, replace, or release this worktree's active effort",
    )
    p.add_argument("action", choices=("bind", "show", "release"))
    p.add_argument(
        "path", nargs="?", default=None, help="Repository-relative effort README path (bind)"
    )
    p.add_argument("--participant", default=None, help="Declared participant identity (bind)")
    p.add_argument(
        "--slice",
        dest="effort_slice",
        default=None,
        help="Declared Plan/Coordination slice (bind)",
    )
    p.add_argument(
        "--replace", action="store_true", help="Explicitly replace an existing binding (bind)"
    )
    p.add_argument(
        "--completed",
        action="store_true",
        help="Release only after the effort is verified Done/archived",
    )
    p.add_argument(
        "--transfer",
        default=None,
        metavar="TARGET",
        help="Release by naming the tracked objective receiving responsibility",
    )
    p.add_argument(
        "--worktree-id", default=None, help="Target worktree id (default: inferred from cwd)"
    )
    p.add_argument("--json", action="store_true")

    p = sub.add_parser(
        "history-digest",
        help="Print a compact recovery digest of this worktree's recent history",
    )
    p.add_argument(
        "--worktree-id", default=None, help="Worktree ID (default: resolved from cwd / session id)"
    )
    p.add_argument(
        "--worktree-dir",
        dest="worktree_dir",
        default=None,
        help="The worktree checkout dir (default: cwd)",
    )
    p.add_argument(
        "--session-id",
        default=None,
        help="Session id for the session->worktree binding fallback "
        "(default: COPILOT_AGENT_SESSION_ID)",
    )
    p.add_argument(
        "--limit", type=int, default=8, help="Max recent entries to include (default: 8)"
    )

    p = sub.add_parser(
        "session-role",
        help="Report this session's role vs the worktree head (head/superseded/...)",
    )
    p.add_argument(
        "--session-id", default=None, help="Session id (default: COPILOT_AGENT_SESSION_ID)"
    )
    p.add_argument(
        "--worktree-id", default=None, help="Worktree ID (default: resolved from cwd / session id)"
    )
    p.add_argument(
        "--worktree-dir",
        dest="worktree_dir",
        default=None,
        help="The worktree checkout dir (default: cwd)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (the default; accepted for command-family consistency)",
    )

    p = sub.add_parser(
        "claimant-liveness",
        help="Report same-machine liveness of an owner_ref "
        "(machine/project/worktree_id) as a tri-state alive/gone/unknown. "
        "The endpoint the reaper's cross-machine claimant probe calls over "
        "SSH; not typically run by hand.",
    )
    p.add_argument("owner_ref", help="Qualified owner ref (machine/project/worktree_id[#session])")
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only)")

    p = sub.add_parser(
        "codename-lookup",
        help="Report whether the active project has a worktree with this "
        "codename ON THIS machine. The endpoint the cross-machine codename "
        "reverse-lookup scan calls over SSH; not typically run by hand.",
    )
    p.add_argument("codename", help="Codename to look up (see 'resolve --codename')")
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only)")


def _resolve_worktree_for_read(worktree_id, worktree_dir=None, session_id=None):
    """Resolve a worktree for read-only hook/introspection commands.

    Resolution order: explicit id -> explicit/current directory (activating that
    repo) -> the session->worktree binding fallback, so HOME-cwd ACP / bare
    resumed sessions still work. Fail-open: returns ``None`` on any problem.
    """
    try:
        if worktree_id:
            return _resolve_worktree_id(worktree_id)
        wdir = worktree_dir or os.getcwd()
        _activate_project_for_path(wdir)
        wid = _infer_worktree_id(None)
        if wid:
            return _resolve_worktree_id(wid)
        if session_id:
            wid = tracking.find_worktree_id_by_session(session_id)
            if wid:
                return _resolve_worktree_id(wid)
    except Exception:
        return None
    return None


def _session_role(record, session_id):
    """Compute a session's role relative to a worktree's asserted head."""
    head = record.resolved_head_session
    head_entry = record.session_entry(head) if head else None
    head_state = head_entry.state if head_entry is not None else None
    registered = bool(session_id and record.session_entry(session_id) is not None)
    pending = _pending_handoff_predecessor_safe(record, exclude=session_id or "")
    is_head = bool(session_id and head == session_id)

    if is_head:
        role = "head"
    elif head and head_state not in _CONCLUDED_STATES:
        role = "superseded" if registered else "unbound"
    elif pending is not None:
        role = "successor-elect"
    else:
        role = "head-elect"
    return {
        "role": role,
        "head_session": head,
        "head_state": head_state,
        "is_head": is_head,
        "registered": registered,
        "pending_handoff_predecessor": (pending.session_id if pending else None),
    }


_CONCLUDED_STATES = ("handed-off", "concluded")


def _pending_handoff_predecessor_safe(record, *, exclude):
    try:
        pending = record.pending_handoffs
        if pending:
            predecessor = record.session_entry(pending[-1].predecessor)
            if predecessor is not None and predecessor.session_id != exclude:
                return predecessor
        for entry in reversed(record.sessions or ()):
            if entry.session_id != exclude and (
                entry.state == "handed-off" and entry.successor is None
            ):
                return entry
        return None
    except Exception:
        return None


def _succession_header(record) -> str:
    """A terse succession/role line for the sessionStart digest, or ``""``."""
    try:
        head = record.resolved_head_session
        pending = _pending_handoff_predecessor_safe(record, exclude="")
        if pending is not None:
            return (
                "Worktree succession: a handoff is pending here (predecessor "
                f"{pending.session_id[-6:]} handed off, no successor yet). If you "
                "are the seeded successor, consume the handoff (see the handoff "
                "entry above) to take over the head; otherwise do not start "
                "parallel work."
            )
        if head:
            entry = record.session_entry(head)
            state = entry.state if entry is not None else "active"
            if state not in _CONCLUDED_STATES:
                return (
                    f"Worktree succession: the current head session on record is "
                    f"{head[-6:]} (active). If that is not you, another session may "
                    "still hold the head -- coordinate rather than starting parallel "
                    "work; if you are resuming it, you are the head."
                )
        return ""
    except Exception:
        return ""


def _effort_orientation(record) -> str:
    """Return the bounded active-effort pointer for the existing conduct hook."""
    try:
        return effort_focus.orientation(Path(record.worktree_path), record.active_effort)
    except Exception:
        return ""


def cmd_session_role(args) -> int:
    """Report THIS session's role relative to its worktree's head (JSON out)."""
    session_id = getattr(args, "session_id", None) or (
        os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    )
    wt_id = _resolve_worktree_for_read(
        getattr(args, "worktree_id", None),
        getattr(args, "worktree_dir", None),
        session_id,
    )
    if not wt_id:
        _json_output({"role": "untracked", "worktree_id": None, "session": session_id})
        return 0
    try:
        record = tracking.load_record(cfg.tracking_dir() / f"{wt_id}.yaml")
    except Exception:
        _json_output({"role": "untracked", "worktree_id": wt_id, "session": session_id})
        return 0
    result = _session_role(record, session_id)
    result["worktree_id"] = wt_id
    result["session"] = session_id
    _json_output(result)
    return 0


def cmd_history_digest(args) -> int:
    """Print a compact recovery digest of THIS worktree's recent history."""
    session_id = getattr(args, "session_id", None) or (
        os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    )
    worktree_id = _resolve_worktree_for_read(
        getattr(args, "worktree_id", None),
        getattr(args, "worktree_dir", None),
        session_id,
    )
    if not worktree_id:
        return 0
    header = ""
    effort = ""
    try:
        record = tracking.load_record(cfg.tracking_dir() / f"{worktree_id}.yaml")
        header = _succession_header(record)
        effort = _effort_orientation(record)
    except Exception:
        header = ""
        effort = ""
    limit = getattr(args, "limit", None) or 8
    semantic = [part for part in (effort, header) if part]
    separator = 2 * len(semantic)
    digest_budget = max(
        0,
        disposition_history.DIGEST_MAX_CHARS - sum(len(part) for part in semantic) - separator,
    )
    text = disposition_history.digest(worktree_id, limit=limit, max_chars=digest_budget)
    combined = "\n\n".join([p for p in (text, *semantic) if p])
    if combined:
        print(combined)
    return 0


def _effort_focus_output(
    record: tracking.WorktreeRecord,
    inspection: effort_focus.EffortInspection | None,
) -> dict[str, object]:
    active = inspection is not None and inspection.active
    return {
        "worktree_id": record.worktree_id,
        "active_effort": inspection.to_dict() if inspection is not None else None,
        "follow_up": record.follow_up or bool(inspection and inspection.active),
        "summary": inspection.summary if active else record.summary,
    }


def _effort_storage_root(config: cfg.Config, worktree_root: Path) -> Path:
    """Resolve the root an effort README path should be read against."""
    try:
        repo_cfg = getattr(config, "default_repo", None)
    except Exception:
        repo_cfg = None
    requires_external = bool(
        getattr(repo_cfg, "stateless", False)
        or getattr(repo_cfg, "requires_external_state_root", False)
    )
    if not requires_external:
        return worktree_root
    try:
        pair = state_root_mod.resolve_pair(config, cwd=str(worktree_root))
    except Exception:
        return worktree_root
    if pair.paired and pair.sibling and not pair.error:
        try:
            return Path(pair.sibling.path).resolve(strict=True)
        except OSError:
            pass
    return worktree_root


def cmd_effort_focus(args) -> int:
    """Bind, show, replace, or release a worktree's canonical effort slice."""
    config = cfg.load_config()
    worktree_id = _infer_worktree_id(getattr(args, "worktree_id", None), config)
    if not worktree_id:
        message = "Could not determine worktree ID. Run inside a worktree or pass --worktree-id."
        if args.json:
            return _json_error(message)
        output.err(message)
        return 1
    worktree_id = _resolve_worktree_id(worktree_id)
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        message = f"Tracking file not found at {yaml_path}."
        if args.json:
            return _json_error(message)
        output.err(message)
        return 1

    action = args.action
    record = tracking.load_record(yaml_path)
    repo_root = None
    repo_error = None
    if action in {"bind", "show"} or (action == "release" and args.completed):
        try:
            repo_root = effort_focus.repository_root(record.worktree_path)
            repo_root = _effort_storage_root(config, repo_root)
        except effort_focus.EffortFocusError as exc:
            repo_error = str(exc)
            if action != "show":
                if args.json:
                    return _json_error(repo_error)
                output.err(repo_error)
                return 1

    if action == "show":
        inspection = (
            (
                effort_focus.inspect_effort(repo_root, record.active_effort)
                if repo_root is not None
                else effort_focus.EffortInspection(
                    ref=record.active_effort,
                    state="stale",
                    reason=repo_error or "tracked worktree is unavailable",
                )
            )
            if record.active_effort is not None
            else None
        )
        payload = _effort_focus_output(record, inspection)
        if args.json:
            _json_output(payload)
        elif inspection is None:
            print(f"No active effort is bound to {record.worktree_id}.")
        else:
            print(
                f"{inspection.state}: {inspection.ref.path} "
                f"[{inspection.ref.participant} / {inspection.ref.slice}]"
            )
            if inspection.reason:
                print(f"Reason: {inspection.reason}")
        return 0

    if action == "bind":
        if repo_root is None:
            raise RuntimeError("repository root validation was not attempted")
        if not args.path:
            message = "bind requires a repository-relative effort README path"
            if args.json:
                return _json_error(message)
            output.err(message)
            return 1
        try:
            ref = effort_focus.make_active_effort(
                args.path, args.participant or "", args.effort_slice or ""
            )
            inspection = effort_focus.validate_binding(repo_root, ref)
        except effort_focus.EffortFocusError as exc:
            if args.json:
                return _json_error(str(exc))
            output.err(str(exc))
            return 1

        global_lock = cfg.tracking_dir() / ".effort-bindings.yaml"
        with tracking._RecordLock(global_lock, require_sidecar=True):
            records = tracking.list_records(cfg.tracking_dir())
            conflict = effort_focus.duplicate_binding(records, worktree_id, record.repo, ref)
            if conflict:
                message = f"that effort participant/slice is already bound to worktree {conflict}"
                if args.json:
                    return _json_error(message)
                output.err(message)
                return 1
            with tracking._RecordLock(yaml_path):
                record = tracking.load_record(yaml_path)
                inspection = effort_focus.validate_binding(repo_root, ref)
                if record.active_effort == ref:
                    payload = _effort_focus_output(record, inspection)
                    if args.json:
                        _json_output(payload)
                    else:
                        print(f"[OK] Effort focus already bound: {ref.path}")
                    return 0
                if record.active_effort is not None and not args.replace:
                    message = (
                        "an effort is already bound; pass --replace to record an "
                        "explicit replacement"
                    )
                    if args.json:
                        return _json_error(message)
                    output.err(message)
                    return 1
                replacing = record.active_effort is not None
                record.active_effort = ref
                record.effort_revision += 1
                tracking.set_disposition(
                    record,
                    summary=inspection.summary,
                    follow_up=True,
                    session_id=os.environ.get("COPILOT_AGENT_SESSION_ID") or None,
                    kind="effort-replace" if replacing else "effort-bind",
                    save=False,
                )
                tracking.save_record(record)
        payload = _effort_focus_output(record, inspection)
        if args.json:
            _json_output(payload)
        else:
            verb = "replaced" if replacing else "bound"
            print(f"[OK] Effort focus {verb}: {ref.path}")
        return 0

    if action == "release":
        if record.active_effort is None:
            if args.json:
                _json_output(_effort_focus_output(record, None))
            else:
                print(f"No active effort is bound to {record.worktree_id}.")
            return 0
        completed = bool(args.completed)
        transfer = (args.transfer or "").strip()
        if completed == bool(transfer):
            message = "release requires exactly one of --completed or --transfer TARGET"
            if args.json:
                return _json_error(message)
            output.err(message)
            return 1
        bound_ref = record.active_effort
        if completed and (
            repo_root is None or not effort_focus.completed_or_archived(repo_root, bound_ref)
        ):
            message = (
                "the bound effort is still open or cannot be verified as Done/"
                "archived; use --transfer for a named responsibility transfer"
            )
            if args.json:
                return _json_error(message)
            output.err(message)
            return 1
        if transfer:
            try:
                transfer = effort_focus.normalize_label(transfer, "transfer target")
            except effort_focus.EffortFocusError as exc:
                if args.json:
                    return _json_error(str(exc))
                output.err(str(exc))
                return 1

        with tracking._RecordLock(yaml_path):
            record = tracking.load_record(yaml_path)
            if record.active_effort != bound_ref:
                message = "effort focus changed while release was being prepared; retry"
                if args.json:
                    return _json_error(message)
                output.err(message)
                return 1
            if completed and (
                repo_root is None or not effort_focus.completed_or_archived(repo_root, bound_ref)
            ):
                message = (
                    "the bound effort changed or no longer satisfies the "
                    "completion gate; retry after resolving it"
                )
                if args.json:
                    return _json_error(message)
                output.err(message)
                return 1
            record.active_effort = None
            record.effort_revision += 1
            slug = PurePosixPath(bound_ref.path).parent.name
            summary = (
                f"Completed effort {slug}"
                if completed
                else f"Transferred effort {slug} to {transfer}"
            )
            tracking.set_disposition(
                record,
                summary=summary,
                follow_up=False,
                session_id=os.environ.get("COPILOT_AGENT_SESSION_ID") or None,
                kind="effort-release",
                save=False,
            )
            tracking.save_record(record)
        payload = _effort_focus_output(record, None)
        if args.json:
            _json_output(payload)
        else:
            print(f"[OK] Effort focus released: {summary}")
        return 0

    message = f"unknown effort-focus action: {action}"
    if args.json:
        return _json_error(message)
    output.err(message)
    return 1


def cmd_claimant_liveness(args) -> int:
    """Report SAME-MACHINE claimant liveness for an owner_ref."""
    alive = claimant_mod.local_claimant_alive(args.owner_ref)
    if getattr(args, "json", False):
        _json_output({"owner_ref": args.owner_ref, "alive": alive})
        return 0
    label = {True: "alive", False: "gone", None: "unknown"}[alive]
    print(f"{args.owner_ref}: {label}")
    return 0


def cmd_codename_lookup(args) -> int:
    """Report whether THIS machine's active project has a matching codename."""
    worktree_id = resolve_worktree_id_by_codename(args.codename)
    if getattr(args, "json", False):
        _json_output(
            {
                "codename": args.codename,
                "found": worktree_id is not None,
                "worktree_id": worktree_id,
            }
        )
        return 0
    if worktree_id:
        print(f"{args.codename}: found -> {worktree_id}")
    else:
        print(f"{args.codename}: not found")
    return 0


def cmd_session_lock(args) -> int:
    """Write or remove a session-state lattice lock (#4272)."""
    from . import locks, sessions

    state_dir = sessions._session_state_dir()
    lock_path = state_dir / args.session / f"{args.kind}.lock"
    if args.action == "remove":
        locks.remove_lock(lock_path)
        if getattr(args, "json", False):
            print(json.dumps({"ok": True, "action": "remove", "path": str(lock_path)}))
        return 0
    extra: dict = {"kind": args.kind, "session_id": args.session}
    if args.worktree:
        extra["worktree_id"] = args.worktree
    ok = locks.write_lock(lock_path, pid=args.pid, extra=extra)
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "ok": ok,
                    "action": "write",
                    "path": str(lock_path),
                    "worktree_id": args.worktree,
                    "pid": args.pid,
                }
            )
        )
    elif not ok:
        print(f"session-lock: failed to write {lock_path}", file=sys.stderr)
    return 0 if ok else 1
