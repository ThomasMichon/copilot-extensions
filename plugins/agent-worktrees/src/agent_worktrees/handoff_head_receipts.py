"""Interpret existing delivery receipts without borrowing head authority."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import tracking
from .handoff_head_io import ENTRY_LIMIT, ObservationFailure, Snapshot
from .tracking_session_activations import session_activation_observation


def segment(value: object) -> str:
    if (
        not isinstance(value, str)
        or value in {".", ".."}
        or re.fullmatch(r"[A-Za-z0-9._-]{1,160}", value) is None
    ):
        raise ObservationFailure("invalid-subject", unavailable=False)
    return value


def timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ObservationFailure("ambiguous-receipt-order", unavailable=False)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        return parsed.astimezone(timezone.utc)
    except ValueError:
        raise ObservationFailure("ambiguous-receipt-order", unavailable=False) from None


@dataclass
class Delivery:
    token: str
    predecessor: str
    source: str
    created: datetime
    delivery_at: datetime
    consumed_at: datetime | None
    consumer: str | None
    author_identity: Any
    consumer_identity: Any
    aborted: bool = False

    def expected(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "predecessor": self.predecessor,
            "consumer": self.consumer,
            "source": self.source,
            "consumed_at": self.consumed_at.isoformat() if self.consumed_at else None,
            "identity": self.consumer_identity,
        }


def _delivery(raw: dict[str, Any], token: str, source: str) -> Delivery:
    task = source == "task"
    if (
        type(raw.get("version")) is not int
        or raw["version"] != (1 if task else 2)
        or raw.get("kind") != ("context-handoff-delivery" if task else "context-handoff")
    ):
        raise ObservationFailure("unsupported-receipt-schema", unavailable=False)
    metadata = raw.get("metadata") if task else raw
    if (
        not isinstance(metadata, dict)
        or type(metadata.get("version")) is not int
        or metadata["version"] != 2
    ):
        raise ObservationFailure("unsupported-receipt-metadata", unavailable=False)
    if metadata.get("kind") != "context-handoff" or metadata.get("storage") != (
        "agent-dispatch" if task else "file"
    ):
        raise ObservationFailure("conflicting-receipt", unavailable=False)
    if (raw.get("handoffToken") if task else raw.get("id")) != token:
        raise ObservationFailure("conflicting-receipt-token", unavailable=False)
    predecessor = segment(metadata.get("sessionId"))
    if task and raw.get("predecessorSession") != predecessor:
        raise ObservationFailure("conflicting-predecessor", unavailable=False)
    if task:
        if metadata.get("taskId") not in (None, token) or not isinstance(raw.get("steps"), dict):
            raise ObservationFailure("conflicting-task-receipt", unavailable=False)
        consumed = raw["steps"].get("taskConsumed")
        attempted = raw["steps"].get("consumeAttempted")
        step_times = raw.get("stepTimes") or {}
        if not isinstance(step_times, dict):
            raise ObservationFailure("invalid-consumption-steps", unavailable=False)
        consumed_at = step_times.get("taskConsumed")
        consumer = raw.get("consumerSession")
        if type(attempted) is not bool or consumed is True and attempted is not True:
            raise ObservationFailure("invalid-consumption-steps", unavailable=False)
        delivery_at = timestamp(raw.get("createdAt"))
        if attempted:
            delivery_at = max(delivery_at, timestamp(step_times.get("consumeAttempted")))
    else:
        consumed = raw.get("consumed")
        consumed_at = raw.get("consumedAt")
        consumer = raw.get("consumedBySession")
        delivery_at = timestamp(metadata.get("createdAt"))
    aborted = raw.get("aborted", False)
    if type(consumed) is not bool or type(aborted) is not bool:
        raise ObservationFailure("invalid-consumption-state", unavailable=False)
    actual_consumption = consumed and not aborted
    created = timestamp(metadata.get("createdAt"))
    observed_at = timestamp(consumed_at) if actual_consumption else None
    if delivery_at < created or observed_at is not None and observed_at < delivery_at:
        raise ObservationFailure("ambiguous-receipt-order", unavailable=False)
    return Delivery(
        token,
        predecessor,
        source,
        created,
        delivery_at,
        observed_at,
        segment(consumer) if actual_consumption else None,
        metadata.get("authorIdentity"),
        raw.get("consumerIdentity"),
        aborted,
    )


def _identity(
    value: Any,
    record: tracking.WorktreeRecord,
    session_id: str | None,
    *,
    current: bool,
) -> bool:
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != 1
        or value.get("project") != record.repo
        or value.get("worktreeId") != record.worktree_id
        or value.get("creationNonce") != record.creation_nonce
        or value.get("sessionId") != session_id
    ):
        return False
    entry = record.session_entry(session_id) if session_id else None
    activation = value.get("activation")
    if entry is None or not isinstance(activation, dict):
        return False
    if (
        type(activation.get("version")) is not int
        or activation["version"] != 1
        or type(activation.get("ordinal")) is not int
        or activation["ordinal"] <= 0
        or not isinstance(activation.get("sourceIdentity"), str)
        or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", activation["sourceIdentity"]) is None
    ):
        return False
    for key in ("startedAt", "startRecordedAt"):
        text = activation.get(key)
        if not isinstance(text, str) or len(text) > 64:
            return False
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return False
    if current:
        return session_activation_observation(entry) == activation
    if entry.source_history_saturated or activation.get("sourceIdentity") in entry.pruned_sources:
        return False
    matches = [
        item
        for item in entry.activations
        if item.ordinal == activation.get("ordinal")
        or item.source_identity == activation.get("sourceIdentity")
    ]
    if len(matches) != 1 or matches[0].source_ambiguous:
        return False
    item = matches[0]
    return activation == {
        "version": 1,
        "ordinal": item.ordinal,
        "startedAt": item.started_at,
        "startRecordedAt": item.start_recorded_at,
        "sourceIdentity": item.source_identity,
    }


def read_expected(
    snapshot: Snapshot,
    record: tracking.WorktreeRecord,
    directory: Path,
    session_root: Path,
) -> Delivery:
    """Read only one exact worktree's bounded directory and registered sessions."""
    candidates: dict[str, str | None] = {}
    mirrors: dict[str, dict[str, Any]] = {}
    for handoff in record.handoffs:
        token, predecessor = segment(handoff.token), segment(handoff.predecessor)
        if token in candidates:
            raise ObservationFailure("ambiguous-handoff-token", unavailable=False)
        candidates[token] = predecessor
    for entry in record.sessions or []:
        sid = segment(entry.session_id)
        request = snapshot.json(session_root / sid / "handoff-request.json")
        if request is None:
            continue
        if (
            request.get("kind") != "context-handoff-session-request"
            or type(request.get("version")) is not int
            or request["version"] != 1
            or request.get("sessionId") != sid
            or request.get("worktree") != record.worktree_id
        ):
            raise ObservationFailure("invalid-predecessor-request", unavailable=False)
        token = segment(request.get("handoffId"))
        if token in candidates and candidates[token] != sid:
            raise ObservationFailure("conflicting-predecessor", unavailable=False)
        candidates[token], mirrors[token] = sid, request
    for name in snapshot.entries(directory):
        if name.endswith(".json") and (
            name.startswith("handoff-") or name.startswith("delivery-")
        ):
            token = name[9:-5] if name.startswith("delivery-") else name[:-5]
            candidates.setdefault(segment(token), None)
    if len(candidates) > ENTRY_LIMIT:
        raise ObservationFailure("observation-delivery-budget")
    deliveries: list[Delivery] = []
    for token, predecessor in candidates.items():
        source = "file" if token.startswith("handoff-") else "task"
        path = directory / (f"{token}.json" if source == "file" else f"delivery-{token}.json")
        raw = snapshot.json(path)
        if raw is None:
            raise ObservationFailure("missing-delivery-receipt", unavailable=False)
        metadata = raw.get("metadata") if source == "task" else raw
        if not isinstance(metadata, dict) or metadata.get("worktree") != record.worktree_id:
            raise ObservationFailure("receipt-subject-mismatch", unavailable=False)
        delivery = _delivery(raw, token, source)
        if predecessor is not None and delivery.predecessor != predecessor:
            raise ObservationFailure("conflicting-predecessor", unavailable=False)
        mirror = mirrors.get(token)
        if mirror is not None and mirror.get("aborted") is True:
            if not delivery.aborted:
                raise ObservationFailure("conflicting-consumption-records", unavailable=False)
        elif mirror is not None and mirror.get("consumed") is True:
            if (
                mirror.get("aborted")
                or delivery.consumer != mirror.get("consumedBySession")
                or delivery.author_identity != mirror.get("authorIdentity")
                or delivery.consumer_identity != mirror.get("consumerIdentity")
            ):
                raise ObservationFailure("conflicting-consumption-records", unavailable=False)
        deliveries.append(delivery)
    consumed = [item for item in deliveries if item.consumed_at is not None]
    if not consumed:
        raise ObservationFailure("no-completed-delivery", unavailable=False)
    consumed.sort(key=lambda item: item.consumed_at, reverse=True)
    newest = consumed[0]
    if len(consumed) > 1 and consumed[1].consumed_at == newest.consumed_at:
        raise ObservationFailure("ambiguous-receipt-order", unavailable=False)
    if any(
        item.consumed_at is None and not item.aborted and item.delivery_at >= newest.delivery_at
        for item in deliveries
    ):
        raise ObservationFailure("newer-incomplete-delivery", unavailable=False)
    if not _identity(newest.author_identity, record, newest.predecessor, current=False):
        raise ObservationFailure("unqualified-author-identity", unavailable=False)
    if not _identity(newest.consumer_identity, record, newest.consumer, current=True):
        raise ObservationFailure(
            "consumer-activation-unqualified-or-superseded", unavailable=False
        )
    return newest
