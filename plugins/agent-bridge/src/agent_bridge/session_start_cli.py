"""Session-start and worktree-read targeting helpers for ``agent-bridge``."""

from __future__ import annotations

import sys


def _core():
    from . import __main__ as core

    return core


def _resolve_read_target(client, target: str) -> tuple[str | None, str | None]:
    from .client import BridgeClientError

    get_session = getattr(client, "get_session", None)
    if get_session is not None:
        try:
            session = get_session(target)
        except BridgeClientError as exc:
            if exc.status != 404:
                raise
        else:
            if session:
                return target, None

    worktree_session_id = _resolve_read_worktree_session(client, target)
    if worktree_session_id:
        return worktree_session_id, target
    return target, None


def _session_status_name(session: dict) -> str:
    status = session.get("status")
    if hasattr(status, "value"):
        status = status.value
    return str(status or "").lower()


def _resolve_read_worktree_session(
    client,
    worktree_id: str,
    *,
    exclude_session_id: str | None = None,
) -> str | None:
    list_sessions = getattr(client, "list_sessions", None)
    if list_sessions is None:
        return None
    try:
        sessions = list_sessions()
    except Exception:
        return None

    fallback: str | None = None
    for session in sessions:
        session_id = str(session.get("session_id") or "")
        if not session_id or session.get("worktree_id") != worktree_id:
            continue
        if exclude_session_id and session_id == exclude_session_id:
            continue
        if _session_status_name(session) in {"created", "starting", "running", "idle"}:
            return session_id
        if fallback is None:
            fallback = session_id
    return fallback


def _wait_for_worktree_read_target(
    client,
    target: str,
    *,
    prior_session_id: str | None = None,
    require_successor: bool = False,
    grace_s: float | None = None,
) -> str | None:
    import time

    core = _core()
    if grace_s is None:
        grace_s = core._STREAM_404_GRACE_S
    deadline = time.monotonic() + max(0.0, grace_s)
    while True:
        session_id = _resolve_read_worktree_session(
            client,
            target,
            exclude_session_id=(prior_session_id if require_successor else None),
        )
        if session_id:
            return session_id
        if time.monotonic() >= deadline:
            return None
        client.refresh_endpoint()
        time.sleep(core._RECONNECT_BACKOFF)


def _report_unavailable_read_target(target: str) -> None:
    print(
        f"\n[RETRY] Session {target} is not currently registered (the bridge "
        "may be mid-restart); if it exists it is preserved and resumable -- "
        "re-run shortly.",
        file=sys.stderr,
    )


def _reuse_existing(client, session: dict, agent_name: str) -> str:
    core = _core()
    sid = session.get("session_id", "")
    name = session.get("name", "")
    turns = session.get("turn_count", 0)
    status = session.get("status", "")
    if status == "stopped":
        print(f"[>] Resuming stopped session {sid} ({name})...")
        try:
            client.resume_session(sid, request_timeout=core._startup_request_timeout(resume=True))
        except Exception:
            pass
    print(f"[>] Reusing session {sid} ({name}) for '{agent_name}' ({turns} prior turn(s))")
    return sid


def _await_coming_up_settled(client, session: dict) -> dict:
    import time as _time

    core = _core()
    if session.get("status", "") not in core._COMING_UP_STATES:
        return session
    sid = session.get("session_id", "")
    name = session.get("name", "")
    print(f"[>] Session {sid} ({name}) is coming up; waiting for it to be ready...")
    deadline = _time.monotonic() + core._COMING_UP_SETTLE_TIMEOUT
    while True:
        try:
            refreshed = client.get_session(sid)
        except Exception:
            refreshed = None
        if refreshed:
            session = refreshed
            if session.get("status", "") not in core._COMING_UP_STATES:
                return session
        if _time.monotonic() >= deadline:
            return session
        _time.sleep(core._COMING_UP_POLL_INTERVAL)


def _find_caller_session(client, agent_name: str, caller_id: str | None) -> dict | None:
    core = _core()
    try:
        sessions = client.list_sessions()
    except Exception:
        return None
    for s in sessions:
        if (
            s.get("agent_name") == agent_name
            and s.get("caller_id") == caller_id
            and s.get("status", "") in core._REUSABLE_SESSION_STATES
            and (s.get("status", "") != "stopped" or bool(s.get("acp_session_id")))
            and not s.get("read_only", False)
        ):
            return s
    return None


def _start_agent_session(
    client,
    agent_name: str,
    *,
    force_new: bool = False,
    refuse_on_conflict: bool = False,
    force: bool = False,
    model: str | None = None,
    effort: str | None = None,
    target_dir: str | None = None,
    worktree_id: str | None = None,
) -> str:
    core = _core()
    from .client import BridgeClientError
    from .session_targeting_cli import _AgentSessionConflict, _busy_session_message

    caller_id = core._get_caller_id()

    if not force_new:
        existing = _find_caller_session(client, agent_name, caller_id)
        if existing is not None:
            existing = _await_coming_up_settled(client, existing)
            settled = existing.get("status", "")
            if settled not in core._REUSABLE_SESSION_STATES:
                pass
            elif settled == "running":
                sid = existing.get("session_id", "")
                if not force:
                    print(_busy_session_message(client, sid, agent_name, caller_id), file=sys.stderr)
                    sys.exit(core._SEND_BUSY_EXIT)
                print(f"[>] --force: ending busy session {sid} to take over...")
                try:
                    client.end_session(sid)
                except Exception:
                    pass
            else:
                return _reuse_existing(client, existing, agent_name)

    print(f"[>] Starting session for agent '{agent_name}'...")
    try:
        resp = client.start_session(
            agent=agent_name,
            target_dir=target_dir,
            caller_id=caller_id,
            sender_repo=core._sender_repo(),
            force_new=force_new,
            caller_owner_ref=core._worktrees_get("owner-ref"),
            worktree_id=worktree_id,
            model=model,
            effort=effort,
            request_timeout=core._startup_request_timeout(),
        )
    except BridgeClientError as exc:
        if _render_head_guard_refusal(exc):
            sys.exit(core._SEND_BUSY_EXIT)
        existing_sid = _conflict_session_id(exc)
        if existing_sid:
            if refuse_on_conflict:
                raise _AgentSessionConflict(agent_name, existing_sid) from exc
            try:
                session = client.get_session(existing_sid)
            except Exception:
                session = {"session_id": existing_sid}
            session.setdefault("session_id", existing_sid)
            session = _await_coming_up_settled(client, session)
            if session.get("status", "") not in core._REUSABLE_SESSION_STATES:
                try:
                    client.end_session(existing_sid)
                except Exception:
                    pass
                return _start_agent_session(
                    client,
                    agent_name,
                    force_new=force_new,
                    refuse_on_conflict=refuse_on_conflict,
                    force=False,
                    model=model,
                    effort=effort,
                )
            if session.get("status", "") == "running":
                if not force:
                    print(_busy_session_message(client, existing_sid, agent_name, caller_id), file=sys.stderr)
                    sys.exit(core._SEND_BUSY_EXIT)
                print(f"[>] --force: ending busy session {existing_sid} to take over...")
                try:
                    client.end_session(existing_sid)
                except Exception:
                    pass
                return _start_agent_session(
                    client,
                    agent_name,
                    force_new=force_new,
                    refuse_on_conflict=refuse_on_conflict,
                    force=False,
                    model=model,
                    effort=effort,
                )
            return _reuse_existing(client, session, agent_name)
        raise
    sid = resp.get("session_id", "")
    name = resp.get("name", "")
    print(f"[>] Session {sid} ({name}) created")
    timeouts = core._phased_timeouts()
    start_timeout = timeouts.codespace_boot if agent_name.startswith("codespace:") else timeouts.session_start
    core._wait_for_idle(client, sid, timeout=start_timeout)
    return sid


def _render_head_guard_refusal(exc) -> bool:
    if getattr(exc, "status", None) != 409:
        return False
    detail = getattr(exc, "detail", None)
    if not isinstance(detail, dict) or detail.get("reason") not in ("worktree_head_active", "worktree_head_pending"):
        return False
    wt = detail.get("worktree_id", "?")
    print(f"[BLOCKED] {detail.get('message', f'Worktree {wt} is occupied.')}", file=sys.stderr)
    for choice in detail.get("choices", []):
        tag = " (preferred)" if choice.get("preferred") else ""
        print(f"  - {choice.get('action')}{tag}: {choice.get('description', '')}", file=sys.stderr)
    override = detail.get("override", f"agent-bridge resume {wt} --force")
    print(f"  Take-over: {override}", file=sys.stderr)
    return True


def _conflict_session_id(exc) -> str | None:
    if getattr(exc, "status", None) != 409:
        return None
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict) and detail.get("error") == "session_conflict":
        return detail.get("existing_session_id")
    return None


def _wait_for_idle(client, session_id: str, timeout: float = 30.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = client.get_session(session_id)
        status = session.get("status", "")
        if status == "idle":
            return
        if status in ("failed", "ended", "stopped"):
            detail = _session_failure_detail(client, session_id)
            suffix = f": {detail}" if detail else ""
            print(f"[FAIL] Session {session_id} entered {status}{suffix}", file=sys.stderr)
            sys.exit(1)
        time.sleep(0.5)
    print(f"[FAIL] Timed out waiting for session {session_id} to become idle", file=sys.stderr)
    sys.exit(1)


def _session_failure_detail(client, session_id: str) -> str | None:
    try:
        events = client.read_range(session_id)
    except Exception:
        return None
    for event in reversed(events):
        data = event.get("data") or {}
        if event.get("event") == "connect_failed":
            message = str(data.get("message") or "").strip()
            stage = data.get("stage")
            stage_name = str(data.get("stage_name") or "").strip()
            if message and stage is not None and stage_name:
                return f"[stage {stage}/{stage_name}] {message}"
            if message:
                return message
        if event.get("event") == "error":
            message = str(data.get("message") or "").strip()
            if message:
                return message
    return None
