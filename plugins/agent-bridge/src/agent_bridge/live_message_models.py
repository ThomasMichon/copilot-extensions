"""Represented live-session inbox requests, receipts, and acknowledgements."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_serializer
from .plugin_readiness import PluginReadinessRequest

LiveMessageDelivery = Literal["queue", "steer", "interrupt"]
DEFAULT_LIVE_REPLY_TIMEOUT = 120.0
MAX_LIVE_REPLY_TIMEOUT = 600.0


def validate_live_reply_timeout(timeout: float | None) -> float:
    """Validate a reply wait before it can become a transport timeout."""
    error = f"must be a finite number of seconds between 0 and {MAX_LIVE_REPLY_TIMEOUT:g}"
    try:
        seconds = float(DEFAULT_LIVE_REPLY_TIMEOUT if timeout is None else timeout)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(error) from None
    if not math.isfinite(seconds) or not 0 <= seconds <= MAX_LIVE_REPLY_TIMEOUT:
        raise ValueError(error)
    return seconds


class SendMessageRequest(BaseModel):
    """Admit an attributed message to a represented live session's inbox."""

    sender: str
    body: str
    reply_to: str | None = None
    kind: str = "prompt"
    delivery: LiveMessageDelivery = "steer"
    wait: bool = False
    wait_timeout: float = Field(
        default=DEFAULT_LIVE_REPLY_TIMEOUT, ge=0.0, le=MAX_LIVE_REPLY_TIMEOUT, allow_inf_nan=False,
    )
    #: Freshness assertion: atomically refuse a changed target incarnation.
    expected_session_id: str | None = None
    #: Stable producer key: an identical retry returns the original message ID.
    idempotency_key: str | None = None
    plugin_readiness: PluginReadinessRequest | None = None

    @model_serializer(mode="wrap")
    def serialize_request(self, handler):
        result = handler(self)
        if self.plugin_readiness is None:
            result.pop("plugin_readiness", None)
        return result


class LiveMessageReceipt(BaseModel):
    """Durable inbox evidence, not a promise of a reply."""

    session_id: str
    message_id: int
    stage: Literal["admitted", "sdk_accepted", "consumed"]
    admitted_at: float
    sdk_accepted_at: float | None = None
    consumed_at: float | None = None


class SendMessageResult(BaseModel):
    """Admission receipt plus optional post-consumption reply evidence.

    A duplicate returns the original ID without waiting for another turn.
    Reply timeout does not imply that delivery failed or remains pending.
    """

    ok: bool = True
    session_id: str
    message_id: int
    duplicate: bool = False
    receipt: LiveMessageReceipt | None = None
    receipt_current: bool = True
    receipt_error: str | None = None
    reply_correlation: Literal["post_consumption_turn"] | None = None
    replied: bool = False
    reply: str | None = None
    stop_reason: str | None = None


class LiveMessage(BaseModel):
    """A pending message awaiting SDK delivery into a live session."""

    id: int
    sender: str
    body: str
    reply_to: str | None = None
    kind: str = "prompt"
    delivery: LiveMessageDelivery = "steer"
    created_at: float
    plugin_readiness: dict | None = None

    @model_serializer(mode="wrap")
    def serialize_message(self, handler):
        result = handler(self)
        if self.plugin_readiness is None:
            result.pop("plugin_readiness", None)
        return result


class LiveMessageListResponse(BaseModel):
    """Pending messages, oldest-first (the extension poll response)."""

    messages: list[LiveMessage]


class AckMessagesRequest(BaseModel):
    """SDK-accepted message IDs, acknowledged after session.send returns."""

    ids: list[int]


class AckMessagesResult(BaseModel):
    """Result of acknowledging SDK-accepted messages."""

    ok: bool = True
    acked: int
