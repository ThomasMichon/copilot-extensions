"""Readiness-bound startup and mode admission for represented sessions."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import HTTPException

from ..live_controls import SetModeResult
from ..plugin_readiness import PluginReadinessError, PluginReadinessRequest


def existing_startup_kind(db, key: str, expectation: PluginReadinessRequest) -> str | None:
    rows = db.execute_read("SELECT kind FROM live_messages WHERE idempotency_key=?", (key,))
    if not rows:
        return None
    kind = rows[0]["kind"]
    if not kind.startswith("startup:prompt:"):
        raise HTTPException(409, detail="operation was already used without startup readiness")
    try:
        control = db.live_control_state(
            expectation.expected_session_id, int(kind.rsplit(":", 1)[1]), include_body=True,
        )
        original = json.loads(control["body"])
        original.pop("selection_digest", None)
        previous = PluginReadinessRequest.model_validate(original)
    except (ValueError, TypeError, KeyError):
        raise HTTPException(409, detail="operation readiness receipt is unavailable") from None
    def selection(value):
        data = value.model_dump(exclude={"timeout_seconds", "expected_session_id"})
        data["plugins"] = sorted(data["plugins"], key=lambda item: item["projected_source"])
        return data
    if selection(previous) != selection(expectation):
        raise HTTPException(409, detail="operation compiled plugin selection changed")
    return kind


async def startup_message(db, body, target, session_id, *, existing_kind, wait_readiness):
    if body.kind != "prompt" or body.delivery != "queue" or not body.idempotency_key:
        raise HTTPException(400, detail="owned startup requires a queued prompt and operation key")
    if target is None:
        raise HTTPException(404, detail="live session not found")
    target_session_id = target["session_id"]
    expectation = body.plugin_readiness.model_copy(update={"expected_session_id": target_session_id})
    if body.plugin_readiness.expected_session_id not in (None, target_session_id):
        raise HTTPException(409, detail="startup native session changed")
    message_kind = existing_kind(db, body.idempotency_key, expectation)
    if message_kind is None:
        try:
            import hashlib

            selection = expectation.model_dump(exclude={"timeout_seconds", "expected_session_id"})
            selection["plugins"] = sorted(selection["plugins"], key=lambda value: value["projected_source"])
            input_digest = hashlib.sha256(json.dumps({
                "selection": selection, "body": body.body, "sender": body.sender,
                "reply_to": body.reply_to, "delivery": body.delivery,
            }, sort_keys=True).encode()).hexdigest()
            observed = await wait_readiness(
                db, session_id, expectation,
                operation_key=body.idempotency_key, operation_digest=input_digest,
            )
        except PluginReadinessError as exc:
            raise HTTPException(409, detail={"code": "plugins_not_ready", "reason": str(exc)}) from exc
        message_kind = existing_kind(db, body.idempotency_key, expectation)
        if message_kind is None:
            message_kind = f"startup:prompt:{observed.control_id}"
    return message_kind, target_session_id


async def set_live_mode(
    db, session_id, body, *, wait_readiness, poll_seconds, claimed_grace_seconds,
    mode_control, ready_mode_control,
) -> SetModeResult:
    expected_mode_session = body.expected_session_id
    control_kind, control_body = mode_control, body.mode
    if body.plugin_readiness is not None:
        try:
            observed = await wait_readiness(db, session_id, body.plugin_readiness)
        except PluginReadinessError as exc:
            raise HTTPException(409, detail={"code": "plugins_not_ready", "reason": str(exc)}) from exc
        if expected_mode_session is not None and expected_mode_session != observed.session_id:
            raise HTTPException(409, detail="mode/readiness native session mismatch")
        expected_mode_session = observed.session_id
        control_kind = ready_mode_control
        control_body = json.dumps({
            "mode": body.mode,
            "plugin_readiness": {
                **body.plugin_readiness.model_dump(),
                "expected_session_id": observed.session_id,
                "selection_digest": observed.selection_digest,
            },
        })
    target = db.get_live_session(session_id)
    target_session_id = target["session_id"] if target is not None else session_id
    control_id, reason = db.enqueue_live_message_if_fresh(
        session_id,
        sender=body.sender,
        body=control_body,
        now=time.time(),
        kind=control_kind,
        delivery="queue",
        expected_session_id=expected_mode_session,
    )
    if reason == "not_found":
        raise HTTPException(status_code=404, detail="live session not found")
    if reason is not None or control_id is None:
        raise HTTPException(
            status_code=409,
            detail=f"live session {session_id} can't take a mode change now ({reason})",
        )
    def settled() -> SetModeResult | None:
        # Follow a session-id change while waiting: the control moves with it.
        # (the DB reads and writes resolve the alias in-statement).
        nonlocal target_session_id
        outcome = (db.live_control_state(target_session_id, control_id) or {}).get("outcome")
        target_session_id = db.resolve_live_session_id(target_session_id)
        if outcome == "applied":
            return SetModeResult(session_id=target_session_id, mode=body.mode, applied=True, state="applied")
        if outcome is not None:
            return SetModeResult(
                session_id=target_session_id, mode=body.mode, applied=False, state="rejected",
                detail="the session couldn't apply it",
            )
        return None

    deadline = time.monotonic() + body.wait_timeout
    while time.monotonic() < deadline:
        if (result := settled()) is not None:
            return result
        await asyncio.sleep(poll_seconds)
    withdrawn = db.withdraw_live_control(target_session_id, control_id, time.time())
    target_session_id = db.resolve_live_session_id(target_session_id)
    if withdrawn:
        return SetModeResult(
            session_id=target_session_id, mode=body.mode, applied=False, state="withdrawn",
            detail=(
                "the session didn't take it in time: its agent-bridge extension may "
                "predate mode changes, or the session isn't responding"
            ),
        )
    # Claimed: the session is applying it; wait briefly for its outcome.
    grace = time.monotonic() + claimed_grace_seconds
    while True:
        if (result := settled()) is not None:
            return result
        if time.monotonic() >= grace:
            break
        await asyncio.sleep(poll_seconds)
    return SetModeResult(
        session_id=target_session_id, mode=body.mode, applied=None, state="in_flight",
        detail="the session took the change but hasn't reported it applied; it may still apply",
    )
