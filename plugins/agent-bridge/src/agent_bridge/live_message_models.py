"""Represented live-session inbox requests, receipts, and acknowledgements."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

LiveMessageDelivery = Literal["queue", "steer", "interrupt"]


class SendMessageRequest(BaseModel):
    """Admit an attributed message to a represented live session's inbox."""

    sender: str
    body: str
    reply_to: str | None = None
    kind: str = "prompt"
    delivery: LiveMessageDelivery = "steer"
    wait: bool = False
    wait_timeout: float = Field(default=120.0, ge=0.0, le=600.0, allow_inf_nan=False)
    #: Freshness assertion: atomically refuse a changed target incarnation.
    expected_session_id: str | None = None
    #: Stable producer key: an identical retry returns the original message ID.
    idempotency_key: str | None = None


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
