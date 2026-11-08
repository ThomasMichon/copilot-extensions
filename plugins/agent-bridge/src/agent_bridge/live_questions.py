"""Current native questions, separate from historical representation and ACP."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .live_question_validation import validate_answer as validate_answer

CAPABILITY = "live_question_answers"
QUESTION_CONTROL = "control:answer-question"
SNAPSHOT_TTL = 15.0
MAX_SNAPSHOTS = 1024
QuestionFamily = Literal["elicitation", "user_input"]
AnswerState = Literal["accepted", "rejected", "unknown", "withdrawn", "in_flight"]


class LiveQuestion(BaseModel):
    family: QuestionFamily
    request_id: str = Field(min_length=1, max_length=512)
    tool_call_id: str | None = Field(default=None, max_length=512)
    message: str = Field(max_length=16000)
    requested_schema: dict[str, Any] | None = None
    choices: list[str] | None = Field(default=None, max_length=100)
    allow_freeform: bool = True
    answerable: bool = False
    reason: str | None = Field(default=None, max_length=240)


class QuestionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation: str = Field(min_length=1, max_length=128)
    pid: int = Field(gt=0)
    process_started_at: float = Field(allow_inf_nan=False)
    requests: list[LiveQuestion] = Field(default_factory=list, max_length=32)


class LiveQuestionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=512)
    family: QuestionFamily
    sender: str = Field(min_length=1, max_length=512)
    action: Literal["accept", "decline", "cancel"] = "accept"
    content: dict[str, Any] = Field(default_factory=dict)
    wait_timeout: float = Field(default=30.0, ge=0.0, le=120.0, allow_inf_nan=False)


class QuestionAnswerAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=512)
    state: Literal["accepted", "rejected", "unknown"]
    detail: str = Field(default="", max_length=240)


@dataclass(frozen=True)
class ObservedQuestions:
    observation: QuestionObservation
    observed_at: float


class LiveQuestions:
    """Short-lived producer snapshots; neither replay nor restart grants authority."""

    def __init__(self) -> None:
        self._snapshots: OrderedDict[str, ObservedQuestions] = OrderedDict()

    def publish(self, session_id: str, observation: QuestionObservation) -> None:
        if len(observation.model_dump_json()) > 128_000:
            raise ValueError("question snapshot exceeds the observation budget")
        keys = [(q.family, q.request_id) for q in observation.requests]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate native question in snapshot")
        self._snapshots[session_id] = ObservedQuestions(observation, time.monotonic())
        self._snapshots.move_to_end(session_id)
        while len(self._snapshots) > MAX_SNAPSHOTS:
            self._snapshots.popitem(last=False)

    def current(self, session_id: str, row: dict[str, Any] | None) -> QuestionObservation | None:
        from .db_core import live_session_is_fresh

        snapshot = self._snapshots.get(session_id)
        if (
            snapshot is None
            or row is None
            or row["session_id"] != session_id
            or not live_session_is_fresh(row, time.time())
            or time.monotonic() - snapshot.observed_at > SNAPSHOT_TTL
            or not matches_process(row, snapshot.observation)
        ):
            return None
        return snapshot.observation


def matches_process(row: dict[str, Any], observation: QuestionObservation) -> bool:
    return (
        row.get("pid") == observation.pid
        and row.get("process_started_at") == observation.process_started_at
    )
