"""Explicit preservation-contract negotiation for authenticated drain control."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DRAIN_CONTRACT_VERSION = 1


class DrainContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    minimum: int = Field(ge=1, le=65535)
    maximum: int = Field(ge=1, le=65535)

    @model_validator(mode="after")
    def ordered(self) -> DrainContract:
        if self.minimum > self.maximum:
            raise ValueError("drain contract minimum exceeds maximum")
        return self

    @property
    def compatible(self) -> bool:
        return self.minimum <= DRAIN_CONTRACT_VERSION <= self.maximum

    def overlaps(self, other: DrainContract) -> bool:
        return self.minimum <= other.maximum and other.minimum <= self.maximum


class DrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    timeout: float = Field(default=300.0, ge=0, le=86400, allow_inf_nan=False)
    poll: float = Field(default=1.0, ge=0.05, le=60, allow_inf_nan=False)
    force: bool = False
    source: str = Field(default="drain-endpoint", min_length=1, max_length=256)
    reason: str | None = Field(default=None, max_length=2048)
    exclude_session_id: str | None = Field(default=None, min_length=1, max_length=1024)
    drain_contract: DrainContract | None = None
    on_incompatible: Literal["refuse", "cold"] = "cold"

    @model_validator(mode="after")
    def graceful_only_cannot_force(self) -> DrainRequest:
        if self.on_incompatible == "refuse" and self.force:
            raise ValueError("graceful-only drain cannot force")
        return self

    @property
    def contract_status(self) -> str:
        if self.drain_contract is None:
            return "legacy-unnegotiated"
        return "compatible" if self.drain_contract.compatible else "incompatible"
