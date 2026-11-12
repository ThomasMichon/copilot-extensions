"""Fresh, read-only delivery attention; durable receipts remain the inbox's facts."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .db_core import live_session_is_fresh

CAPABILITY = "live_delivery_observation"
OBSERVATION_TTL = 15.0
MAX_OBSERVATIONS = 1024


class DeliveryStateObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pid: int = Field(gt=0)
    process_started_at: float = Field(allow_inf_nan=False)
    generation: str = Field(min_length=1, max_length=128)
    complete: bool = False
    muted: bool = False
    pending: list[Literal["question", "permission"]] = Field(default_factory=list, max_length=2)


@dataclass(frozen=True)
class _Observed:
    value: DeliveryStateObservation
    observed_at: float


class LiveDeliveryState:
    def __init__(self) -> None:
        self._observations: OrderedDict[str, _Observed] = OrderedDict()

    def publish(self, session_id: str, value: DeliveryStateObservation) -> None:
        if len(set(value.pending)) != len(value.pending):
            raise ValueError("duplicate pending-input category")
        self._observations[session_id] = _Observed(value, time.monotonic())
        self._observations.move_to_end(session_id)
        while len(self._observations) > MAX_OBSERVATIONS:
            self._observations.popitem(last=False)

    def inspect(self, row: dict | None) -> dict:
        unknown = {"availability": "unavailable", "state": "unknown", "reasons": []}
        if row is None:
            return {**unknown, "reason": "live registration unavailable"}
        observed = self._observations.get(row["session_id"])
        if (
            observed is None or not live_session_is_fresh(row, time.time())
            or time.monotonic() - observed.observed_at > OBSERVATION_TTL
            or row.get("pid") != observed.value.pid
            or row.get("process_started_at") != observed.value.process_started_at
        ):
            return {**unknown, "reason": "fresh native delivery observation unavailable"}
        value = observed.value
        reasons = list(value.pending)
        if value.muted:
            reasons.append("muted")
        if reasons:
            return {
                "availability": "available", "state": "blocked", "reasons": reasons,
                "reason": "pending native input or muted inbox; receipt facts are unchanged",
            }
        if not value.complete:
            return {**unknown, "reason": "observer joined mid-turn; absence of blockers is unknown"}
        return {"availability": "available", "state": "ready", "reasons": [], "reason": None}
