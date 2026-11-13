"""Versioned local consumer observations, not authentication credentials."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasPath, BaseModel, ConfigDict, Field, model_serializer

from .inbox_custody import InboxConsumer
from .live_inbox_runtime import InboxBinding
from .live_message_models import LiveMessage

INBOX_CONTRACT: Literal["retained-inbox-v1"] = "retained-inbox-v1"
InboxStage = Literal["offered", "sending", "unknown", "sdk_accepted", "consumed"]


class InboxConsumerInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    session_id: str = Field(min_length=1)
    incarnation: str = Field(min_length=1)
    pid: int | None = Field(default=None, gt=0)
    process_started_at: float | None = Field(default=None, gt=0)
    portable_launch: str | None = Field(default=None, pattern=r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$")

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        value = handler(self)
        if self.portable_launch is None:
            value.pop("portable_launch", None)
        return value

    def observation(self) -> InboxConsumer:
        return InboxConsumer(**self.model_dump())


class InboxBindingInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    installation: str = Field(min_length=1)
    epoch: int = Field(gt=0)

    def observation(self) -> InboxBinding:
        return InboxBinding(**self.model_dump())


class InboxNegotiateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    contract: Literal["retained-inbox-v1"]
    consumer: InboxConsumerInfo


class InboxNegotiationInfo(BaseModel):
    contract: Literal["retained-inbox-v1"] = INBOX_CONTRACT
    binding: InboxBindingInfo
    expected_incarnation: str | None


class InboxCurrentRequest(InboxNegotiateRequest):
    binding: InboxBindingInfo


class InboxEnrollRequest(InboxCurrentRequest):
    # Required even for the first enrollment: omission must not mean "replace".
    expected_incarnation: str | None


class InboxPollRequest(InboxCurrentRequest):
    limit: int | None = Field(default=None, ge=1, le=100)


class InboxHandoffRequest(InboxCurrentRequest):
    message_id: int = Field(gt=0)
    handoff_id: str = Field(min_length=1)


class InboxOutcomeRequest(InboxNegotiateRequest):
    message_id: int = Field(gt=0)
    handoff_id: str = Field(min_length=1)
    outcome: Literal["sdk_accepted", "consumed", "unknown"]


class RetainedInboxMessage(LiveMessage):
    model_config = ConfigDict(populate_by_name=True)

    handoff_id: str = Field(validation_alias=AliasPath("handoff", "id"))
    handoff_stage: InboxStage
    consumer: InboxConsumerInfo | None = None


class InboxPollResult(BaseModel):
    contract: Literal["retained-inbox-v1"] = INBOX_CONTRACT
    binding: InboxBindingInfo
    consumer: InboxConsumerInfo
    messages: list[RetainedInboxMessage]


class InboxBeginResult(BaseModel):
    # True authorizes only the invocation receiving this response, never a retry.
    begin: bool


class InboxOutcomeResult(BaseModel):
    stage: InboxStage


class InboxHandoffReceipt(BaseModel):
    """Original observations only; neither a message body nor send permission."""

    session_id: str
    message_id: int
    handoff_id: str
    consumer: InboxConsumerInfo
    stage: InboxStage
    admitted_at: float
    handoff_started_at: float | None
    sdk_accepted_at: float | None
    consumed_at: float | None


class InboxRecoveryResult(BaseModel):
    messages: list[InboxHandoffReceipt]
    next_after: int | None
