"""Strict hinted intake with bounded reads and settled queue admission."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .indexing.change_hints import ChangeHints
from .indexing.change_hints_contract import validate_scope

if TYPE_CHECKING:
    from .indexing.task_store import TaskRecord, TaskStore

MAX_HINT_REQUEST_BYTES = 1_048_576


class HintedReindexRequest(BaseModel):
    """Required hints cannot be dropped into a legacy source-only request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: str
    full: bool = False
    change_hints: ChangeHints

    @field_validator("change_hints", mode="before")
    @classmethod
    def parse_hints(cls, value: object) -> ChangeHints:
        """Validate the entire versioned domain envelope before admission."""
        return ChangeHints.from_dict(value)

    @model_validator(mode="after")
    def check_scope(self) -> HintedReindexRequest:
        """Validate exact-source incremental scope before queue effects."""
        validate_scope(self.source, self.full, self.change_hints)
        return self


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate request field")
        result[key] = value
    return result


async def read_hinted_request(request: Request) -> HintedReindexRequest:
    """Read one bounded request; reject duplicate fields and unsupported data."""
    async def read_body() -> bytes:
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_HINT_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Change-hints request too large")
            data.extend(chunk)
        return bytes(data)

    try:
        raw = await asyncio.wait_for(read_body(), timeout=5)
        payload = json.loads(raw, object_pairs_hook=_unique_fields)
        return HintedReindexRequest.model_validate(payload)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="Change-hints request timed out") from None
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=422, detail="Invalid change-hints v1 request") from None


async def enqueue_hinted_request(
    store: TaskStore, body: HintedReindexRequest, notify: Callable[[], None],
) -> TaskRecord:
    """Offload SQLite without relinquishing caller admission during cancellation."""
    from .indexing.runner import _settle

    async def enqueue_and_notify() -> TaskRecord:
        task = await asyncio.to_thread(
            store.enqueue,
            source=body.source,
            full=False,
            change_hints=body.change_hints,
            trigger_source="api:agent_index_reindex_hints",
        )
        notify()
        return task

    return await _settle(asyncio.create_task(enqueue_and_notify()))
