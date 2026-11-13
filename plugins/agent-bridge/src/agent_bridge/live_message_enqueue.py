"""Atomic registration, enrolled-consumer selection and original-key recovery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import hashlib
import re
import sqlite3
from typing import TYPE_CHECKING

from .db_core import live_session_is_fresh
from .db_live_session_aliases import CANONICAL_SESSION_SQL as _CANON
from .inbox_custody import InboxConsumer
from .plugin_readiness import PluginReadinessRequest, PluginReadinessResult, startup_input_digest
from .runtime_custody import RuntimeAdmission, RuntimeCustodyError, admission_document

if TYPE_CHECKING:
    from .db import Database


@dataclass(frozen=True)
class LiveMessageEnqueue:
    session_id: str
    sender: str
    body: str
    now: float
    reply_to: str | None
    kind: str
    delivery: str
    expected_session_id: str | None
    idempotency_key: str | None
    stale_seconds: float
    report_duplicate: bool
    require_idle: bool
    exact_session: bool = False


def _requires_retained_admission(request: LiveMessageEnqueue) -> bool:
    return request.delivery == "interrupt" or (
        request.kind != "control:plugin-readiness"
        and request.kind.casefold().startswith(("control:", "startup:"))
    )


def _ready_startup(conn: sqlite3.Connection, request: LiveMessageEnqueue) -> bool:
    matched = re.fullmatch(r"startup:prompt:([1-9][0-9]*)", request.kind)
    if matched is None or request.delivery != "queue" or not request.idempotency_key:
        return False
    try:
        control = conn.execute(
            "SELECT sender,body,outcome FROM live_messages WHERE id=? AND session_id=? "
            "AND kind='control:plugin-readiness'",
            (int(matched[1]), request.session_id),
        ).fetchone()
        if control is None:
            return False
        selected = json.loads(control["body"])
        digest = selected.pop("selection_digest")
        expectation = PluginReadinessRequest.model_validate(selected)
        observed = PluginReadinessResult.model_validate_json(control["outcome"])
        intent = conn.execute(
            "SELECT sender FROM live_messages WHERE session_id=? AND kind='control:plugin-readiness' "
            "AND idempotency_key=?",
            (
                request.session_id,
                "startup-readiness-" + hashlib.sha256(request.idempotency_key.encode()).hexdigest(),
            ),
        ).fetchone()
        return (
            intent is not None and intent["sender"] == control["sender"]
            and expectation.expected_session_id == observed.session_id == request.session_id
            and observed.status == "ready" and observed.selection_digest == digest
            and control["sender"] == "bridge-startup:" + startup_input_digest(
                expectation, body=request.body, sender=request.sender,
                reply_to=request.reply_to, delivery=request.delivery,
            )
        )
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def _duplicate(
    db: Database, request: LiveMessageEnqueue, association: str | None, *,
    original_association: bool = False,
) -> tuple[int | None, str | None] | None:
    if not request.idempotency_key:
        return None
    msg_canon = (
        "COALESCE((SELECT target_session_id FROM live_session_aliases "
        "WHERE alias_session_id = live_messages.session_id), live_messages.session_id)"
    )
    existing = db.execute_read(
        "SELECT id, session_id, sender, body, reply_to, kind, delivery, exact_session, runtime_custody_json, handoff_json, "
        f"{msg_canon} = {_CANON} AS same_target, "
        f"(? IS NULL OR {_CANON} = {_CANON}) AS same_expected "
        "FROM live_messages WHERE idempotency_key = ?",
        (request.session_id, request.session_id, request.expected_session_id,
         request.expected_session_id, request.expected_session_id,
         request.session_id, request.session_id, request.idempotency_key),
    )
    if not existing:
        return None
    row = existing[0]
    retained = bool(row["exact_session"]) or row["runtime_custody_json"] is not None or row["handoff_json"] is not None
    same_target = row["session_id"] == request.session_id if retained or request.exact_session else bool(row["same_target"])
    same_expected = (
        request.expected_session_id is None or request.expected_session_id == row["session_id"]
    ) if retained or request.exact_session else bool(row["same_expected"])
    if (
        same_target and same_expected
        and row["sender"] == request.sender and row["body"] == request.body
        and row["reply_to"] == request.reply_to and row["kind"] == request.kind
        and row["delivery"] == request.delivery
        and bool(row["exact_session"]) == request.exact_session
        and (original_association or row["runtime_custody_json"] == association)
    ):
        return int(row["id"]), ("duplicate" if request.report_duplicate else None)
    return None, "idempotency_conflict"


def _enrolled_admission(
    db: Database, conn: sqlite3.Connection, request: LiveMessageEnqueue,
    frontend: RuntimeAdmission | None, refusal: str | None,
) -> RuntimeAdmission | None:
    if request.kind in ("prompt", "notify", "status-check") and request.delivery in ("steer", "queue"):
        return None
    rows = conn.execute(
        "SELECT * FROM bridge_inbox_consumers "
        f"WHERE current=1 AND (session_id=? OR session_id={_CANON}) LIMIT 1",
        (request.session_id, request.session_id, request.session_id),
    ).fetchall()
    if not rows:
        target = conn.execute(
            "SELECT venue FROM live_sessions WHERE session_id=? AND cli_mode=1",
            (request.session_id,),
        ).fetchone()
        if target is not None and target["venue"] is not None:
            try:
                venue = json.loads(target["venue"])
            except (TypeError, ValueError):
                venue = None
            if isinstance(venue, dict) and venue.get("mux_endpoint") is not None:
                raise RuntimeCustodyError("Portable inbox requires its enrolled launch consumer")
        history = conn.execute(
            f"SELECT 1 FROM bridge_inbox_consumers WHERE session_id=? OR session_id={_CANON} LIMIT 1",
            (request.session_id, request.session_id, request.session_id),
        ).fetchone()
        if history is None and conn.execute(
            f"SELECT 1 FROM live_messages WHERE (session_id=? OR session_id={_CANON}) "
            "AND (runtime_custody_json IS NOT NULL OR handoff_json IS NOT NULL) LIMIT 1",
            (request.session_id, request.session_id, request.session_id),
        ).fetchone() is None:
            return None
    if (
        rows and request.idempotency_key and request.idempotency_key.strip()
        and _ready_startup(conn, request)
    ):
        return None
    if refusal is not None:
        raise RuntimeCustodyError(refusal)
    if frontend is None or not rows or rows[0]["current"] != 1:
        raise RuntimeCustodyError("Retained inbox requires current frontend and consumer enrollment")
    if not request.idempotency_key or not request.idempotency_key.strip():
        raise RuntimeCustodyError("Retained inbox admission requires an original operation key")
    if (
        request.delivery not in ("steer", "queue") or request.kind.casefold().startswith("control:")
        or (request.kind.casefold().startswith("startup:") and not _ready_startup(conn, request))
    ):
        raise RuntimeCustodyError("Retained inbox control, startup and interrupt effects are unsupported")
    consumer = InboxConsumer(**json.loads(rows[0]["consumer_json"]))
    if consumer.session_id != request.session_id:
        raise RuntimeCustodyError("Retained inbox admission requires the exact native session id")
    db._inbox_admission(conn, frontend.installation, frontend)
    db._current_inbox_consumer(
        conn, consumer, request.now, frontend.installation, frontend.epoch,
    )
    return frontend


def enqueue_fresh(
    db: Database, request: LiveMessageEnqueue, admission: RuntimeAdmission | None, *,
    enrolled_inbox: bool, inbox_refusal: str | None,
) -> tuple[int | None, str | None]:
    """Select custody in the insert's transaction; keyed retries retain their owner."""
    if enrolled_inbox:
        with db._runtime_transaction(write=True) as conn:
            replay = _duplicate(db, request, None, original_association=True)
            if replay is not None:
                return replay
            selected = _enrolled_admission(db, conn, request, admission, inbox_refusal)
            result = _insert(db, request, selected, conn.execute)
            if result[0] is not None and result[1] is None:
                from .compatible_inbox import retain_message

                retain_message(conn, result[0])
            return result
    return _insert(
        db, request, admission,
        lambda statement, params: db._execute_runtime_admitted_write(statement, params, admission),
    )


def _insert(
    db: Database, request: LiveMessageEnqueue, admission: RuntimeAdmission | None,
    execute: Callable[[str, tuple[object, ...]], sqlite3.Cursor],
) -> tuple[int | None, str | None]:
    association = admission_document(admission)
    cutoff = request.now - request.stale_seconds
    cur = execute(
        "INSERT OR IGNORE INTO live_messages "
        "(session_id, sender, body, reply_to, kind, delivery, "
        "idempotency_key, created_at, consumption_tracked, exact_session, runtime_custody_json) "
        f"SELECT {_CANON}, ?, ?, ?, ?, ?, ?, ?, 1, ?, ? "
        "WHERE EXISTS ("
        "  SELECT 1 FROM live_sessions ls "
        f"  WHERE ls.session_id = {_CANON} AND ls.status = 'live' "
        "    AND ls.updated_at >= ? "
        "    AND ("
        "      ls.worktree_id IS NULL OR ls.session_id = ("
        "        SELECT session_id FROM live_sessions "
        "        WHERE worktree_id = ls.worktree_id AND status = 'live' "
        "          AND (ls.machine IS NULL OR ls.repo IS NULL OR "
        "            (machine = ls.machine COLLATE NOCASE AND repo = ls.repo COLLATE NOCASE)) "
        "          AND updated_at >= ? "
        "        ORDER BY registered_at DESC, updated_at DESC LIMIT 1"
        "      )"
        "    )"
        f"    AND (? IS NULL OR {_CANON} = ls.session_id)"
        "    AND (? = 0 OR (ls.session_id=? AND ls.session_id=?))"
        "    AND (? = 0 OR (ls.turn_state IN ('idle','completed') "
        "      AND NOT EXISTS (SELECT 1 FROM live_messages pending "
        "        WHERE pending.session_id=ls.session_id AND pending.consumed_at IS NULL "
        "        AND (pending.delivered_at IS NULL OR pending.consumption_tracked=1) "
        "        AND pending.kind NOT LIKE 'control:%')))"
        ") AND (? = 0 OR ? IS NOT NULL OR NOT EXISTS ("
        f"SELECT 1 FROM bridge_inbox_consumers WHERE session_id=? OR session_id={_CANON}))",
        (
            request.session_id, request.session_id, request.sender, request.body,
            request.reply_to, request.kind, request.delivery, request.idempotency_key,
            request.now, request.exact_session, association, request.session_id, request.session_id, cutoff, cutoff,
            request.expected_session_id, request.expected_session_id, request.expected_session_id,
            request.exact_session, request.session_id, request.expected_session_id,
            request.require_idle,
            _requires_retained_admission(request),
            association, request.session_id, request.session_id, request.session_id,
        ),
    )
    if cur.rowcount == 1:
        return int(cur.lastrowid or 0), None
    replay = _duplicate(db, request, association)
    if replay is not None:
        return replay
    if association is None and _requires_retained_admission(request) and db.execute_read(
        f"SELECT 1 FROM bridge_inbox_consumers WHERE session_id=? OR session_id={_CANON} LIMIT 1",
        (request.session_id, request.session_id, request.session_id),
    ):
        return None, "retained_inbox_requires_admission"
    row = db.get_live_session(request.session_id)
    if row is None:
        return None, "not_found"
    if not live_session_is_fresh(row, request.now, request.stale_seconds):
        return None, "stale"
    current_sid = request.session_id
    if row.get("worktree_id"):
        scoped = row.get("machine") is not None and row.get("repo") is not None
        current_sid = db.current_live_session_for_worktree(
            row["worktree_id"], now=request.now, stale_seconds=request.stale_seconds,
            machine=row.get("machine") if scoped else None,
            project=row.get("repo") if scoped else None,
        ) or request.session_id
    if current_sid != request.session_id:
        return None, f"superseded:{current_sid}"
    if request.expected_session_id is not None and request.expected_session_id != current_sid:
        return None, f"expected_mismatch:{current_sid}"
    return None, "session_busy" if request.require_idle else "stale"
