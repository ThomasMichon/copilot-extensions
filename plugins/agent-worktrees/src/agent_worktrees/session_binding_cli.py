"""Session binding / handoff-note CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import os
import sys

import yaml

from . import activity, output, profile_assignment, sessions, tracking
from . import config as cfg, session_context as session_context_mod


def _core():
    from . import __main__ as core

    return core


def _json_error(*args, **kwargs):
    return _core()._json_error(*args, **kwargs)


def _json_output(*args, **kwargs):
    return _core()._json_output(*args, **kwargs)


def _read_hook_stdin(*args, **kwargs):
    return _core()._read_hook_stdin(*args, **kwargs)


def _hook_event_timestamp(*args, **kwargs):
    return _core()._hook_event_timestamp(*args, **kwargs)


def _status_monitor_enabled(*args, **kwargs):
    return _core()._status_monitor_enabled(*args, **kwargs)


def _ensure_status_monitor(*args, **kwargs):
    return _core()._ensure_status_monitor(*args, **kwargs)


def _activate_project_for_path(*args, **kwargs):
    return _core()._activate_project_for_path(*args, **kwargs)


def _adopt_linked_worktree(*args, **kwargs):
    return _core()._adopt_linked_worktree(*args, **kwargs)


def _resolve_mux_worktree_id(*args, **kwargs):
    return _core()._resolve_mux_worktree_id(*args, **kwargs)


def _activate_project_for_worktree_id(*args, **kwargs):
    return _core()._activate_project_for_worktree_id(*args, **kwargs)


def _emit_register_session_result(*args, **kwargs):
    return _core()._emit_register_session_result(*args, **kwargs)


def _spawn_status_updater(*args, **kwargs):
    return _core()._spawn_status_updater(*args, **kwargs)


def _maybe_emit_stage_13(*args, **kwargs):
    return _core()._maybe_emit_stage_13(*args, **kwargs)


def _resolve_worktree_id(*args, **kwargs):
    return _core()._resolve_worktree_id(*args, **kwargs)


def _resolve_active_project(*args, **kwargs):
    return _core()._resolve_active_project(*args, **kwargs)


def _find_tracking_file_by_session(*args, **kwargs):
    return _core()._find_tracking_file_by_session(*args, **kwargs)


def _session_handoff_token() -> str:
    return getattr(_core(), "_SESSION_HANDOFF_TOKEN")


def _session_bind_session_key() -> str:
    return getattr(_core(), "_SESSION_BIND_SESSION")


def _session_bind_worktree_key() -> str:
    return getattr(_core(), "_SESSION_BIND_WORKTREE")


def _session_bind_project_key() -> str:
    return getattr(_core(), "_SESSION_BIND_PROJECT")


def add_parsers(sub) -> None:
    sp = sub.add_parser("register-session", help="Register a Copilot session against a worktree")
    sp.add_argument(
        "--worktree-id", default=None, help="Worktree ID (resolved from --cwd when omitted)"
    )
    sp.add_argument(
        "--session-id",
        default=None,
        help="Copilot session ID (read from --stdin payload when omitted)",
    )
    sp.add_argument(
        "--cwd",
        default=None,
        help="Session cwd, used to resolve the worktree when --worktree-id is absent",
    )
    sp.add_argument(
        "--stdin",
        action="store_true",
        help="Read the Copilot sessionStart JSON payload from stdin",
    )
    sp.add_argument(
        "--pid", type=int, default=None, help="PID of the Copilot process (diagnostic only)"
    )
    sp.add_argument("--pane", default=None, help="Mux pane id (defaults to TMUX_PANE/PSMUX_PANE)")
    sp.add_argument(
        "--launch-id",
        dest="launch_id",
        default=None,
        help="Launch-flow correlation id (from WORKTREE_LAUNCH_ID)",
    )
    sp.add_argument(
        "--assignment-token",
        dest="assignment_token",
        default=None,
        help="Profile-assignment launch token (normally from the session environment)",
    )
    sp.add_argument(
        "--emit-context",
        action="store_true",
        help="Emit sessionStart additionalContext for the recovered binding",
    )
    sp.add_argument(
        "--handoff-token",
        default=None,
        help="Exact pending handoff token this session is consuming",
    )
    sp.add_argument(
        "--handoff-candidate-token",
        default=None,
        help="Pending token this newly-created session is a "
        "candidate to consume; associates without takeover",
    )

    sp = sub.add_parser("deregister-session", help="Mark a Copilot session as ended on a worktree")
    sp.add_argument(
        "--worktree-id",
        default=None,
        help="Worktree ID (resolved from cwd or launch binding when omitted)",
    )
    sp.add_argument(
        "--session-id",
        default=None,
        help="Copilot session ID (read from --stdin payload when omitted)",
    )
    sp.add_argument("--cwd", default=None, help="Session cwd from the sessionEnd payload")
    sp.add_argument(
        "--stdin", action="store_true", help="Read the Copilot sessionEnd JSON payload from stdin"
    )
    sp.add_argument(
        "--launch-id",
        dest="launch_id",
        default=None,
        help="Launch-flow correlation id (from WORKTREE_LAUNCH_ID)",
    )

    sp = sub.add_parser(
        "bind-session",
        help="Explicitly bind the current Copilot session to a worktree it declares",
    )
    sp.add_argument(
        "--worktree-dir",
        dest="worktree_dir",
        default=None,
        help="The worktree checkout dir the session is in (default: cwd)",
    )
    sp.add_argument(
        "--worktree-id", default=None, help="Worktree ID (alternative to --worktree-dir)"
    )
    sp.add_argument(
        "--session-id", default=None, help="Copilot session ID (default: COPILOT_AGENT_SESSION_ID)"
    )
    sp.add_argument("--pane", default=None, help="Mux pane id (defaults to TMUX_PANE/PSMUX_PANE)")
    sp.add_argument(
        "--pid", type=int, default=None, help="PID of the Copilot process (diagnostic only)"
    )
    sp.add_argument(
        "--handoff-token", default=None, help="Exact pending handoff token this successor consumes"
    )
    sp.add_argument(
        "--assignment-token",
        dest="assignment_token",
        default=None,
        help="Profile-assignment launch token (normally from the session environment)",
    )

    sp = sub.add_parser(
        "bind-nudge",
        help="Hook: emit an additionalContext nudge when an active worktree is unbound",
    )
    sp.add_argument(
        "--cwd", default=None, help="The session's working directory (default: process cwd)"
    )
    sp.add_argument(
        "--stdin",
        action="store_true",
        help="Read the Copilot postToolUse JSON payload from stdin (for workingDirectory)",
    )

    sp = sub.add_parser(
        "note-handoff",
        help="Append a handoff reference to this worktree's history (record-first recovery)",
    )
    sp.add_argument(
        "--task",
        default=None,
        help="The agent-dispatch handoff task id (the pointer to the full brief)",
    )
    sp.add_argument("--title", default=None, help="A one-line topic for the handoff")
    sp.add_argument(
        "--worktree-dir",
        dest="worktree_dir",
        default=None,
        help="The worktree checkout dir (default: cwd)",
    )
    sp.add_argument(
        "--worktree-id", default=None, help="Worktree ID (alternative to --worktree-dir)"
    )
    sp.add_argument(
        "--session-id",
        default=None,
        help="Predecessor session ID (default: COPILOT_AGENT_SESSION_ID)",
    )


def _emit_handoff_claim_stages(
    wt_id: str, session_id: str, linked_handoff, *, launch_id: str | None = None
) -> None:
    """Emit Stage 10 (claimed) always; Stage 11 (predecessor closing) only if
    the resident-monitor's own retire flow hasn't already recorded it.

    ``linked_handoff`` is ``tracking.register_session()``'s return: the
    ``SessionHandoff`` it *just* linked (head authoritatively transferred), or
    ``None`` for no fresh transfer. The monitor's `handoff_predecessor_retire`
    (outcome="gone") is ALSO mapped to Stage 11 (Phase 1) and can fire before
    this call site (retirement is gated on candidate presence, not on this
    link) -- checking for it first prevents a double Stage-11 record for the
    same token while still covering the common case where that outcome never
    fires (observed "left-running", not "gone" -- Phase 4's open question).
    """
    if linked_handoff is None:
        return
    from . import handoff_diagnostics

    activity.log_event(
        "handoff_successor_claimed",
        worktree_id=wt_id,
        session_id=session_id,
        handoff_token=linked_handoff.token,
        predecessor_session_id=linked_handoff.predecessor,
        successor_session_id=session_id,
        launch_id=launch_id,
    )
    already_retired = any(
        str(e.get("handoff_token") or "").strip() == linked_handoff.token
        and e.get("outcome") == "gone"
        for e in activity.read_events(worktree_id=wt_id, event="handoff_predecessor_retire")
    )
    if not already_retired:
        activity.log_event(
            "handoff_pickup_confirmed_predecessor_closing",
            worktree_id=wt_id,
            session_id=linked_handoff.predecessor,
            successor_session_id=session_id,
            handoff_token=linked_handoff.token,
            launch_id=launch_id,
        )
    handoff_diagnostics.stamp_session_state_handoff(
        linked_handoff.predecessor,
        handoff_token=linked_handoff.token,
        successor_session_id=session_id,
    )
    _maybe_emit_stage_13(wt_id, linked_handoff.token, launch_id=launch_id)


def cmd_register_session(args: argparse.Namespace) -> int:
    """Register a Copilot session against a worktree (hook-invoked)."""
    wt_id = getattr(args, "worktree_id", None)
    session_id = getattr(args, "session_id", None)
    cwd = getattr(args, "cwd", None)
    pid = getattr(args, "pid", None)
    event_at = None
    source = "hook"
    resident_environment = getattr(args, "resident_environment", False)
    pane_id = getattr(args, "pane", None)
    if not pane_id and not resident_environment:
        pane_id = os.environ.get("TMUX_PANE") or os.environ.get("PSMUX_PANE") or None
    if isinstance(pane_id, str):
        pane_id = pane_id.strip() or None

    payload = getattr(args, "hook_payload", None)
    if getattr(args, "stdin", False):
        payload = _read_hook_stdin()
    if isinstance(payload, dict) and payload:
        session_id = session_id or payload.get("sessionId")
        cwd = cwd or payload.get("cwd")
        event_at = _hook_event_timestamp(payload)
        hook_source = payload.get("source")
        if isinstance(hook_source, str) and hook_source.strip():
            source = f"hook:{hook_source.strip()}"

    if not session_id and not resident_environment:
        session_id = os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    if not session_id:
        return 0
    if _status_monitor_enabled():
        _ensure_status_monitor()

    if not wt_id:
        wt_id = _activate_session_binding(session_id)

    if not wt_id and cwd:
        _activate_project_for_path(cwd, force=True)
        try:
            wt_id = tracking.find_worktree_id_by_cwd(cwd)
        except Exception:
            wt_id = None
        if not wt_id:
            wt_id = _adopt_linked_worktree(cwd)

    mux_binding = None
    if not wt_id or not pane_id:
        try:
            mux_binding = sessions.mux_binding_for_session(session_id)
        except Exception:
            mux_binding = None
    if not wt_id and mux_binding:
        candidate = mux_binding.get("worktree_id")
        candidate = _resolve_mux_worktree_id(candidate) or candidate
        if candidate and _activate_project_for_worktree_id(candidate):
            wt_id = candidate
    if not wt_id:
        if getattr(args, "emit_context", False):
            from . import session_projection

            report = session_projection.recovery_report(session_id, cwd=cwd)
            message = session_projection.render_recovery_context(report)
            _emit_register_session_result(
                args,
                {"additionalContext": message} if message else {},
            )
        return 0
    if not cfg.active_project():
        _activate_project_for_worktree_id(wt_id)
    recovered_mux = (
        mux_binding
        if mux_binding
        and sessions.mux_session_name(mux_binding.get("worktree_id") or "")
        == sessions.mux_session_name(wt_id)
        else None
    )
    if recovered_mux:
        pane_id = pane_id or mux_binding.get("pane_id")
        pid = pid or mux_binding.get("copilot_pid")

    candidate_token = getattr(args, "handoff_candidate_token", None) or (
        None if resident_environment else os.environ.get(_session_handoff_token())
    ) or None
    candidate_associated = False
    candidate_predecessor = None
    linked_handoff = None
    try:
        linked_handoff = tracking.register_session(
            wt_id,
            session_id,
            pid=pid,
            pane_id=pane_id,
            started_at=event_at,
            source=source,
            handoff_token=getattr(args, "handoff_token", None),
            candidate_token=candidate_token,
            initial_projection=source == "hook:new",
        )
        if candidate_token:
            yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
            with tracking._RecordLock(yaml_path):
                candidate_record = tracking.load_record(yaml_path)
                candidate_handoff = next(
                    (item for item in candidate_record.handoffs if item.token == candidate_token),
                    None,
                )
                candidate_predecessor = (
                    candidate_handoff.predecessor if candidate_handoff is not None else None
                )
                tracking.associate_handoff_candidate(
                    candidate_record,
                    candidate_token,
                    session_id,
                    associated_at=event_at,
                    save=False,
                )
                tracking.save_record(candidate_record, yaml_path)
            candidate_associated = True
    except Exception as e:
        output.err(f"Failed to register session: {e}")
        return 1
    try:
        binding_record = tracking.load_record(cfg.tracking_dir() / f"{wt_id}.yaml")
        from . import handoff_diagnostics

        handoff_diagnostics.stamp_session_state_worktree_binding(
            session_id,
            wt_id,
            worktree_dir=binding_record.worktree_path,
            machine=binding_record.machine,
        )
    except Exception:
        pass
    profile_assignment.bind(
        getattr(args, "assignment_token", None)
        or (
            None
            if resident_environment
            else os.environ.get(profile_assignment.ASSIGNMENT_TOKEN_ENV)
        ),
        session_id,
        wt_id,
    )
    profile_assignment.maintain()
    launch_id = getattr(args, "launch_id", None) or (
        None if resident_environment else os.environ.get("WORKTREE_LAUNCH_ID")
    )
    activity.log_event(
        "session_started",
        worktree_id=wt_id,
        session_id=session_id,
        launch_id=launch_id,
    )
    if candidate_associated:
        from . import handoff_diagnostics

        handoff_diagnostics.stamp_session_state_handoff(
            session_id,
            handoff_token=candidate_token,
            predecessor_session_id=candidate_predecessor,
        )
        activity.log_event(
            "handoff_successor_session_start_bound",
            worktree_id=wt_id,
            session_id=session_id,
            handoff_token=candidate_token,
            predecessor_session_id=candidate_predecessor,
            launch_id=launch_id,
        )
    _emit_handoff_claim_stages(wt_id, session_id, linked_handoff, launch_id=launch_id)
    record = None
    try:
        rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        if rec_path.exists():
            record = tracking.load_record(rec_path)
    except Exception:
        record = None
    upd_path = cwd
    if record is not None:
        root = os.path.normcase(os.path.normpath(record.worktree_path))
        candidate = os.path.normcase(os.path.normpath(cwd)) if cwd else ""
        try:
            inside = bool(candidate) and os.path.commonpath([candidate, root]) == root
        except ValueError:
            inside = False
        if not inside:
            upd_path = record.worktree_path
    _spawn_status_updater(wt_id, upd_path)

    if getattr(args, "emit_context", False):
        target_cwd = record.worktree_path if record is not None else cwd or "<unknown>"
        message = ""
        if record is not None:
            try:
                context_config = cfg.load_config(include_control_plane_related_pr=False)
                registry_context = session_context_mod.render_registry_context(
                    context_config,
                    record,
                    cwd=target_cwd,
                    pane_id=pane_id,
                    mux_session=(recovered_mux.get("session_name") if recovered_mux else None),
                    plugin_related_anchors=getattr(args, "plugin_related_anchors", None),
                )
                message = f"[agent-worktrees] This Copilot session is bound.\n{registry_context}"
            except Exception:
                message = ""
        if not message and recovered_mux:
            message = (
                f"[agent-worktrees] This Copilot session is inside mux session "
                f"{recovered_mux['session_name']}, pane {pane_id} (recovered "
                "from the session lock PID and process ancestry). Its authoritative "
                f"worktree id is {wt_id}; run task commands from {target_cwd}. "
                "Treat this binding as authoritative even when TMUX/PSMUX "
                "environment variables or the session payload cwd are absent."
            )
        elif not message and pane_id:
            message = (
                f"[agent-worktrees] This Copilot session reports mux pane "
                f"{pane_id} and is bound to worktree {wt_id}; run task commands "
                f"from {target_cwd}."
            )
        elif not message:
            message = (
                f"[agent-worktrees] This Copilot session is bound to worktree "
                f"{wt_id}; run task commands from {target_cwd}."
            )
        if candidate_associated:
            message += (
                "\n[agent-worktrees] This started session is associated with "
                f"pending handoff token {candidate_token}; the predecessor "
                "remains head until this successor explicitly consumes and "
                "acknowledges the handoff."
            )
        _emit_register_session_result(args, {"additionalContext": message})
    return 0


def cmd_bind_session(args: argparse.Namespace) -> int:
    """Explicitly bind THIS Copilot session to a worktree it declares (JSON out)."""
    session_id = getattr(args, "session_id", None) or (
        os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    )
    if not session_id:
        return _json_error(
            "could not determine the session id to bind: pass --session-id "
            "(COPILOT_AGENT_SESSION_ID was not set)",
            exit_code=2,
        )

    pane_id = (
        getattr(args, "pane", None)
        or os.environ.get("TMUX_PANE")
        or os.environ.get("PSMUX_PANE")
        or None
    )
    if isinstance(pane_id, str):
        pane_id = pane_id.strip() or None

    wt_id = getattr(args, "worktree_id", None)
    wdir = getattr(args, "worktree_dir", None) or os.getcwd()
    if wt_id:
        if not cfg.active_project() and not _activate_project_for_worktree_id(wt_id):
            return _json_error(
                f"could not find the adopted project that owns worktree '{wt_id}'",
                exit_code=3,
            )
        try:
            wt_id = _resolve_worktree_id(wt_id)
        except Exception as e:
            return _json_error(f"could not resolve worktree '{wt_id}': {e}")
    else:
        _activate_project_for_path(wdir)
        try:
            wt_id = tracking.find_worktree_id_by_cwd(wdir)
        except Exception:
            wt_id = None
    if not wt_id:
        return _json_error(
            f"'{wdir}' is not inside a tracked worktree; pass --worktree-dir "
            "pointing at the worktree checkout (or --worktree-id)",
            exit_code=3,
        )

    handoff_token = getattr(args, "handoff_token", None)
    candidate_before_ack = None
    if handoff_token:
        try:
            before_record = tracking.load_record(cfg.tracking_dir() / f"{wt_id}.yaml")
            pending = next(
                (item for item in before_record.handoffs if item.token == handoff_token),
                None,
            )
            candidate_before_ack = pending.candidate if pending else None
        except Exception:
            candidate_before_ack = None

    try:
        linked_handoff = tracking.register_session(
            wt_id,
            session_id,
            pid=getattr(args, "pid", None),
            pane_id=pane_id,
            source="bind",
            handoff_token=handoff_token,
        )
        profile_assignment.bind(
            getattr(args, "assignment_token", None)
            or os.environ.get(profile_assignment.ASSIGNMENT_TOKEN_ENV),
            session_id,
            wt_id,
        )
    except Exception as e:
        return _json_error(f"failed to bind session: {e}")

    activity.log_event(
        "session_bound",
        worktree_id=wt_id,
        session_id=session_id,
        source="bind-session",
        pane=pane_id,
    )
    _emit_handoff_claim_stages(
        wt_id,
        session_id,
        linked_handoff,
        launch_id=os.environ.get("WORKTREE_LAUNCH_ID"),
    )

    from . import disposition_history

    disposition_history.append(
        wt_id,
        at=tracking._now_iso(),
        summary="",
        title=None,
        follow_up=False,
        changed=[],
        kind="bind",
        session_id=session_id,
    )

    upd_path = None
    try:
        rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        if rec_path.exists():
            upd_path = tracking.load_record(rec_path).worktree_path
    except Exception:
        upd_path = None
    _spawn_status_updater(wt_id, upd_path or wdir)

    try:
        record = tracking.load_record(cfg.tracking_dir() / f"{wt_id}.yaml")
        head = record.resolved_head_session
    except Exception:
        head = None
    _json_output(
        {
            "worktree_id": wt_id,
            "session": session_id,
            "pane": pane_id,
            "bound": True,
            "head_session": head,
            "handoff_token": handoff_token,
            "candidate_before_ack": candidate_before_ack,
            "candidate_acknowledged": bool(handoff_token and candidate_before_ack == session_id),
        }
    )
    return 0


_BIND_NUDGE_COOLDOWN_S = 90


def _bind_nudge_should_fire(record) -> bool:
    """Whether an active agent in this worktree looks UNBOUND and should be nudged."""
    if record is None:
        return False
    try:
        return record.resolved_head_session is None
    except Exception:
        return False


def cmd_note_handoff(args: argparse.Namespace) -> int:
    """Append a session-tagged ``handoff`` entry to this worktree's history."""
    from . import disposition_history

    session_id = getattr(args, "session_id", None) or (
        os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    )
    wt_id = getattr(args, "worktree_id", None)
    wdir = getattr(args, "worktree_dir", None) or os.getcwd()
    if wt_id:
        wt_id = _resolve_worktree_id(wt_id)
    else:
        _activate_project_for_path(wdir)
        try:
            wt_id = tracking.find_worktree_id_by_cwd(wdir)
        except Exception:
            wt_id = None
    if not wt_id:
        _json_output({"noted": False, "reason": "not a tracked worktree"})
        return 0

    task = (getattr(args, "task", None) or "").strip()
    title = (getattr(args, "title", None) or "").strip()
    parts = []
    if title:
        parts.append(title)
    if task:
        parts.append(f"handoff task {task}")
    summary = " -- ".join(parts) if parts else "handoff stored"

    handoff_ordinal = None
    if task and session_id:
        try:
            yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
            existing = tracking.load_record(yaml_path)
            if existing.session_entry(session_id) is None:
                tracking.register_session(
                    wt_id,
                    session_id,
                    source="handoff",
                )
            with tracking._RecordLock(yaml_path):
                record = tracking.load_record(yaml_path)
                handoff = tracking.open_handoff(
                    record,
                    session_id,
                    task,
                    save=False,
                )
                tracking.save_record(record, yaml_path)
                handoff_ordinal = handoff.ordinal
        except Exception:
            handoff_ordinal = None

    disposition_history.append(
        wt_id,
        at=tracking._now_iso(),
        summary=summary,
        title=None,
        follow_up=False,
        changed=[],
        kind="handoff",
        session_id=session_id,
    )
    _json_output(
        {
            "noted": True,
            "worktree_id": wt_id,
            "session": session_id,
            "task": task or None,
            "handoff_ordinal": handoff_ordinal,
        }
    )
    return 0


def _bind_nudge_decision(cwd: str, *, deadline: float | None = None) -> dict:
    """Return the advisory bind reminder without importing a CLI subprocess."""
    import time as _time

    try:
        _activate_project_for_path(cwd)
        try:
            wt_id = tracking.find_worktree_id_by_cwd(cwd)
        except Exception:
            return {}
        if not wt_id:
            return {}
        yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        if not yaml_path.exists():
            return {}
        record = tracking.load_record(yaml_path)
        if not _bind_nudge_should_fire(record):
            return {}

        stamp = cfg.tracking_dir() / f"{wt_id}.bind-nudge-at"
        now = _time.time()
        try:
            if stamp.exists() and (now - stamp.stat().st_mtime) < _BIND_NUDGE_COOLDOWN_S:
                return {}
        except Exception:
            pass

        wdir = record.worktree_path or cwd
        msg = (
            "[agent-worktrees] This worktree has no session bound on record, but "
            "you are working in it -- so its live tracking (status bar, picker) is "
            "inactive. If you started here (or changed directory in) without being "
            "registered, restore the binding by running:\n"
            f"    agent-worktrees bind-session --worktree-dir={wdir}\n"
            "This is a one-time declaration; once bound, this reminder stops."
        )
        if deadline is not None and _time.time() >= deadline:
            return {}
        try:
            stamp.write_text(str(now), encoding="utf-8")
        except Exception:
            pass
        return {"additionalContext": msg}
    except Exception:
        return {}


def cmd_bind_nudge(args: argparse.Namespace) -> int:
    """postToolUse hook: nudge an unbound-but-active session to bind (JSON out)."""
    import json as _json

    def _emit(obj) -> int:
        try:
            sys.stdout.write(_json.dumps(obj))
        except Exception:
            pass
        return 0

    try:
        cwd = getattr(args, "cwd", None)
        if not cwd and getattr(args, "stdin", False):
            payload = _read_hook_stdin()
            if payload:
                cwd = payload.get("workingDirectory") or payload.get("cwd")
        return _emit(_bind_nudge_decision(cwd or os.getcwd()))
    except Exception:
        return _emit({})


def _activate_session_binding(session_id: str | None) -> str | None:
    """Activate and return a scoped bare-resume worktree binding."""
    if not session_id or os.environ.get(_session_bind_session_key()) != session_id:
        return None
    worktree_id = os.environ.get(_session_bind_worktree_key())
    project = os.environ.get(_session_bind_project_key())
    if not worktree_id or not project:
        return None
    try:
        resolved, _anchor = _resolve_active_project(project)
        if not resolved:
            return None
        cfg.set_active_project(resolved)
    except Exception:
        return None
    return worktree_id


def cmd_deregister_session(args: argparse.Namespace) -> int:
    """Mark a Copilot session as ended on a worktree (hook-invoked)."""
    wt_id = getattr(args, "worktree_id", None)
    session_id = getattr(args, "session_id", None)
    cwd = getattr(args, "cwd", None)
    event_at = None
    source = "hook"
    if getattr(args, "stdin", False):
        payload = _read_hook_stdin()
        if payload:
            session_id = session_id or payload.get("sessionId")
            cwd = cwd or payload.get("cwd")
            event_at = _hook_event_timestamp(payload)
            hook_source = payload.get("source")
            if isinstance(hook_source, str) and hook_source.strip():
                source = f"hook:{hook_source.strip()}"
    if not session_id:
        session_id = os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    if not session_id:
        return 0
    if not wt_id:
        wt_id = _activate_session_binding(session_id)
    if not wt_id and cwd:
        _activate_project_for_path(cwd)
        try:
            wt_id = tracking.find_worktree_id_by_cwd(cwd)
        except Exception:
            wt_id = None
    if not wt_id:
        yaml_path = _find_tracking_file_by_session(session_id)
        if yaml_path is not None:
            try:
                wt_id = tracking.load_record(yaml_path).worktree_id
                _activate_project_for_worktree_id(wt_id)
            except Exception:
                wt_id = None
    if wt_id:
        if not cfg.active_project():
            _activate_project_for_worktree_id(wt_id)
        try:
            wt_id = _resolve_worktree_id(wt_id)
        except Exception:
            return 0
    if not wt_id:
        return 0
    try:
        tracking.deregister_session(
            wt_id,
            session_id,
            ended_at=event_at,
            source=source,
        )
        _capture_session_title(wt_id, session_id)
    except Exception as e:
        output.err(f"Failed to deregister session: {e}")
        return 1
    activity.log_event(
        "session_ended",
        worktree_id=wt_id,
        session_id=session_id,
        launch_id=getattr(args, "launch_id", None) or os.environ.get("WORKTREE_LAUNCH_ID"),
    )
    return 0


def _capture_session_title(worktree_id: str, session_id: str) -> bool:
    """Read summary/name from the session's workspace.yaml and persist it."""
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return False

    rec = tracking.load_record(yaml_path)
    if rec.title and rec.title != "null":
        return False

    session_dir = sessions._session_state_dir() / session_id
    ws_file = session_dir / "workspace.yaml"
    if not ws_file.exists():
        return False
    if sessions._is_detached_session(session_dir):
        return False

    try:
        with open(ws_file, encoding="utf-8") as f:
            ws_data = yaml.safe_load(f)
    except Exception:
        return False

    if not ws_data or not isinstance(ws_data, dict):
        return False

    _placeholder = ("", "|-", "|", ">-", ">", "null", "Untitled")
    display_text = ""
    summary = ws_data.get("summary", "")
    if isinstance(summary, str) and summary.strip() and summary not in _placeholder:
        display_text = summary.strip()
    if not display_text:
        name = ws_data.get("name", "")
        if isinstance(name, str) and name.strip() and name not in _placeholder:
            display_text = name.strip()

    if display_text:
        rec.title = display_text
        tracking.save_record(rec)
        return True
    return False
