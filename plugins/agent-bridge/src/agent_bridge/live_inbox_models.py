"""Versioned local consumer observations, not authentication credentials."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasPath, BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from .inbox_custody import InboxConsumer
from .live_inbox_runtime import InboxBinding
from .live_message_models import LiveMessage

INBOX_CONTRACT: Literal["retained-inbox-v1"] = "retained-inbox-v1"
InboxStage = Literal["offered", "sending", "unknown", "sdk_accepted", "consumed", "rejected"]


class InboxConsumerInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    session_id: str = Field(min_length=1)
    incarnation: str = Field(min_length=1)
    message_base: Literal[1] | None = None
    pid: int | None = Field(default=None, gt=0)
    process_started_at: float | None = Field(default=None, gt=0)
    portable_launch: str | None = Field(default=None, pattern=r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$")

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        value: dict[str, Any] = handler(self)
        if self.portable_launch is None:
            value.pop("portable_launch", None)
        if self.message_base is None:
            value.pop("message_base", None)
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
    receiving_api: Literal["native-sdk-v1"] | None = None
    message_base: Literal[1] | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        value: dict[str, Any] = handler(self)
        if self.receiving_api is None:
            value.pop("receiving_api", None)
        if self.message_base is None:
            value.pop("message_base", None)
        return value

    @model_validator(mode="after")
    def legacy_observations(self) -> InboxNegotiateRequest:
        if self.receiving_api is None and (
            self.consumer.pid is None or self.consumer.process_started_at is None
        ):
            raise ValueError("Legacy receiving requires consumer process observations")
        return self


class InboxNegotiationInfo(BaseModel):
    contract: Literal["retained-inbox-v1"] = INBOX_CONTRACT
    binding: InboxBindingInfo
    expected_incarnation: str | None
    message_base: Literal[1] = 1


class InboxCurrentRequest(InboxNegotiateRequest):
    binding: InboxBindingInfo


class InboxEnrollRequest(InboxCurrentRequest):
    # Required even for the first enrollment: omission must not mean "replace".
    expected_incarnation: str | None


class InboxPollRequest(InboxCurrentRequest):
    limit: int | None = Field(default=None, ge=1, le=100)
    # Idle receivers only: also return these started originals that were never
    # marked submitted and that the receiver verified absent from its history.
    reattempt: list[int] = Field(default_factory=list, max_length=100)


class InboxHandoffRequest(InboxCurrentRequest):
    message_id: int = Field(gt=0)
    handoff_id: str = Field(min_length=1)
    # The polled attempt count; required to re-grant a started original.
    attempt: int | None = Field(default=None, ge=0)


class InboxOutcomeRequest(InboxNegotiateRequest):
    message_id: int = Field(gt=0)
    handoff_id: str = Field(min_length=1)
    outcome: Literal["sdk_accepted", "consumed", "unknown"]


class RetainedInboxMessage(LiveMessage):
    model_config = ConfigDict(populate_by_name=True)

    handoff_id: str | None = Field(default=None, validation_alias=AliasPath("handoff", "id"))
    handoff_stage: InboxStage
    message_contract: dict[str, object] | None = None
    control: bool = False
    consumer: InboxConsumerInfo | None = None
    attempt: int | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        value: dict[str, Any] = handler(self)
        if self.consumer is None:
            value.pop("consumer", None)
        if self.attempt is None:
            value.pop("attempt", None)
        if self.message_contract is None:
            value.pop("message_contract", None)
        if not self.control:
            value.pop("control", None)
        return value


class InboxDispositionRequest(InboxCurrentRequest):
    message_id: int = Field(gt=0)
    state: Literal["rejected", "started", "completed", "unknown"]
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    reason: str = Field(min_length=1, max_length=1024)


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
    disposition: dict[str, object] | None = None


class InboxRecoveryResult(BaseModel):
    messages: list[InboxHandoffReceipt]
    next_after: int | None
