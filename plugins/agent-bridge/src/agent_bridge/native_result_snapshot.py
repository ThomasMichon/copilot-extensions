"""Native-scoped observations without relabelling retained managed history."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .models import DelegatedResultSnapshot, ResultField, ResultFidelity, ResultIncrement
from .result_snapshot import (
    DEFAULT_MAX_ITEMS,
    DEFAULT_MAX_TEXT_CHARS,
    build_owned_result_snapshot as legacy_snapshot,
    build_represented_result_snapshot as legacy_represented_snapshot,
)
from .result_tokens import ResultHistoryChangedError
from .runtime_custody import RuntimeCustodyError
from .turn_custody import original_handoff

if TYPE_CHECKING:
    from .db import Database
    from .events import EventLog
    from .session_manager import Session


def build_represented_result_snapshot(
    *,
    registration: dict[str, Any],
    event_log: EventLog,
    requested_ref: str,
    position: str | None,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    retired_ids: frozenset[str] = frozenset(),
    inbox: list[dict[str, Any]] | None = None,
    native_only: bool = False,
) -> DelegatedResultSnapshot:
    """Keep combined legacy history; refuse it when native origin is required."""
    if native_only and (
        registration["session_id"] != requested_ref
        or retired_ids
        or event_log._session_id != requested_ref
    ):
        raise ResultHistoryChangedError(
            "represented history has no native content-origin contract after aliasing",
        )
    return legacy_represented_snapshot(
        registration=registration,
        event_log=event_log,
        requested_ref=requested_ref,
        position=position,
        max_items=max_items,
        max_text_chars=max_text_chars,
        retired_ids=retired_ids,
        inbox=inbox,
    )


def build_owned_result_snapshot(
    *,
    db: Database,
    session: Session,
    requested_ref: str,
    position: str | None,
    max_items: int = DEFAULT_MAX_ITEMS,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    native_only: bool = False,
) -> DelegatedResultSnapshot:
    """Keep legacy reads; expose content in native mode only with its original origin."""
    snapshot = legacy_snapshot(
        db=db,
        session=session,
        requested_ref=requested_ref,
        position=position,
        max_items=max_items,
        max_text_chars=max_text_chars,
    )
    if not native_only:
        return snapshot

    latest = snapshot.latest_result
    value = latest.value
    index = value.get("turn_index") if isinstance(value, dict) else None
    if latest.availability in {"available", "partial"}:
        row = db.get_turn(session.session_id, index) if type(index) is int else None
        try:
            handoff = original_handoff(row) if row is not None else {}
        except RuntimeCustodyError:
            handoff = {}
        consumer = handoff.get("consumer")
        if (
            not isinstance(consumer, dict)
            or not snapshot.identity.native_session_id
            or consumer.get("native_session_id") != snapshot.identity.native_session_id
        ):
            latest = ResultField(
                availability="unsupported_for_target",
                reason="the completed turn's native conversation origin is unconfirmed",
            )
    unavailable = ResultField(
        availability="unsupported_for_target",
        reason="managed event and client projections have no native content-origin contract",
    )
    snapshot.latest_result = latest
    snapshot.incremental = ResultIncrement(
        availability="not_yet_observed",
        reason="managed event history has no native content-origin contract",
    )
    snapshot.state = snapshot.state.model_copy(
        update={
            "active_work": unavailable,
            "pending_input": unavailable,
            "attention": unavailable,
            "context_pct": None,
            "usage_model": None,
        }
    )
    snapshot.fidelity = ResultFidelity(
        level="reduced",
        event_retention="durable",
        unavailable=["native_event_origin", "native_client_content_origin"],
    )
    value = latest.value
    snapshot.limits.used_text_chars = sum(
        len(text)
        for key in ("text", "stop_reason")
        if isinstance(value, dict) and isinstance(text := value.get(key), str)
    )
    return snapshot
