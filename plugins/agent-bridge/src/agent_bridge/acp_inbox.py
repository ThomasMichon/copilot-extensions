"""ACP receiving adapter for the coordinator's existing message queue."""

from __future__ import annotations

from html import escape
import asyncio
from itertools import islice
import time

from . import compatible_inbox
from .inbox_custody import InboxConsumer, original_handoff
from .models import SessionStatus
from .runtime_custody import RuntimeCustodyError


def is_live(session) -> bool:
    return bool(
        session is not None and session.status in (SessionStatus.IDLE, SessionStatus.RUNNING)
        and session.client is not None and session.client.is_running
    )


def register(manager, session) -> None:
    """Advertise a connected receiver, not permission to start or resume it."""
    if not is_live(session):
        return
    db = manager.db
    now = time.time()
    status = db.register_live_session(
        session.session_id, machine=None, cwd=session.target.cwd,
        worktree_id=session.target.worktree_id, repo=session.target.project,
        branch=None, pid=None, role="acp", driven_by=session.session_id, now=now,
    )
    if status != "live":
        raise RuntimeCustodyError(f"ACP inbox registration refused: {status}")
    compatible_inbox.enroll(db, consumer(session), now=now)


def consumer(session) -> InboxConsumer:
    return InboxConsumer(
        session.session_id, f"acp:{session.session_id}:{session.acp_session_id}",
        None, None,
    )


def resolve(manager, handle: str):
    """Use only the manager's already connected sessions."""
    session = manager.get_session(handle)
    if session is None:
        matches = [
            item for item in manager.list_sessions()
            if is_live(item) and (
                item.target.worktree_id == handle
                or str(item.agent_name or "").casefold() == handle.casefold()
            )
        ]
        if len(matches) > 1:
            raise ValueError("Several live ACP receivers match; use an exact session id")
        session = matches[0] if matches else None
    if is_live(session):
        register(manager, session)
        return manager.db.get_live_session(session.session_id)
    return None


def render(message: dict) -> str:
    attrs = [f'from="{escape(message["sender"], quote=True)}"', f'msg-id="{message["id"]}"']
    if message.get("reply_to"):
        attrs.append(f'reply-to="{escape(message["reply_to"], quote=True)}"')
    kind = message["kind"]
    if kind != "prompt":
        attrs.append(f'kind="{escape(kind, quote=True)}"')
    guidance = {
        "notify": "This is informational; no new work or reply is expected.",
        "status-check": "Answer tersely; do not treat this as new work.",
    }.get(kind)
    if message.get("reply_to"):
        guidance = (guidance + " " if guidance else "") + (
            "To reply, use the session's agent-bridge command catalog with "
            '`send <reply-to> "..."`; from is only a display label.'
        )
    body = message["body"] + (f"\n\n({guidance})" if guidance else "")
    return f'<agent-message {" ".join(attrs)}>\n{body}\n</agent-message>'


def record(db, handoff: dict, outcome: str) -> None:
    db.record_live_message_handoff(
        InboxConsumer(**handoff["consumer"]), handoff["message_id"], handoff["id"],
        outcome=outcome, now=time.time(),
    )


def attention(session) -> dict:
    unknown = {
        "availability": "unavailable", "state": "unknown", "reasons": [],
        "reason": "complete ACP input-wait observation unavailable",
    }
    if not is_live(session):
        return unknown
    reasons = []
    permission = getattr(session.client, "_pending_permission_future", None)
    if isinstance(permission, asyncio.Future) and not permission.done():
        reasons.append("permission")
    questions = getattr(session.client, "_pending_elicitations", None)
    if isinstance(questions, dict) and any(
        isinstance(future, asyncio.Future) and not future.done()
        for future in islice(questions.values(), 64)
    ):
        reasons.append("question")
    return (
        {"availability": "available", "state": "blocked", "reasons": reasons,
         "reason": "pending ACP input; original receipt remains accepted"}
        if reasons else unknown
    )


async def deliver(manager, session) -> None:
    """Admit one queued input through the existing ACP turn owner, never replay."""
    async with session._turn_start_lock:
        if (
            not is_live(session) or session.status != SessionStatus.IDLE
            or manager.get_session(session.session_id) is not session
        ):
            return
        register(manager, session)
        with manager.db._runtime_transaction(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM live_messages WHERE session_id=? "
                "AND runtime_custody_json IS NULL AND kind IN ('prompt','notify','status-check') "
                "AND delivery IN ('queue','steer') AND consumed_at IS NULL "
                "AND delivered_at IS NULL AND handoff_started_at IS NULL "
                "ORDER BY id LIMIT 1", (session.session_id,),
            ).fetchone()
            if row is None:
                return
            compatible_inbox.retain_message(conn, row["id"])
            message = dict(conn.execute(
                "SELECT * FROM live_messages WHERE id=?", (row["id"],),
            ).fetchone())
            handoff = original_handoff(message)
        if not compatible_inbox.begin(
            manager.db, InboxConsumer(**handoff["consumer"]),
            message["id"], handoff["id"], now=time.time(),
        ):
            return
        try:
            await manager._submit_prompt_locked(
                session.session_id, render(message), no_resume=True,
                ordinary_handoff=handoff,
            )
        except BaseException:
            record(manager.db, handoff, "unknown")
            raise


async def refresh(manager) -> None:
    for session in list(manager._sessions.values()):
        if not is_live(session):
            continue
        try:
            register(manager, session)
            await deliver(manager, session)
        except Exception:
            from .session_manager import log

            log.warning("ACP inbox refresh failed for %s", session.session_id, exc_info=True)


async def wait_reply(db, session_id: str, message_id: int, timeout: float) -> dict:
    """Observe this queued input's own turn, not whichever turn ends next."""
    deadline = time.monotonic() + timeout
    while True:
        rows = db.execute_read(
            "SELECT response_text,stop_reason,completed_at FROM turns "
            "WHERE session_id=? AND live_message_id=?", (session_id, message_id),
        )
        if rows and rows[0]["completed_at"] is not None:
            row = rows[0]
            return {
                "replied": not str(row["stop_reason"] or "").startswith("error:"),
                "reply": row["response_text"], "stop_reason": row["stop_reason"],
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"replied": False}
        await asyncio.sleep(min(0.25, remaining))
