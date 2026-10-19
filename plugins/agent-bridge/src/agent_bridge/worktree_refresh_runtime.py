"""Detached refresh evidence without transferring discovery admission."""

from __future__ import annotations

import json
import math
from dataclasses import asdict

from zdd.private_protocol import PrivateIPCError, Refusal

from .resolver_runtime import _encode
from .routes.worktree_refresh import SourceEvidence, WorktreeRefreshOwner

_FIELDS = (
    "epoch", "generation", "closed", "available", "initialized",
    "last_attempt", "last_duration", "last_outcome",
)


def _validate(value: object) -> dict:
    if (
        type(value) is not dict or set(value) != {*_FIELDS, "sources"}
        or any(type(value[key]) is not int or value[key] < 0 for key in ("epoch", "generation"))
        or any(type(value[key]) is not bool for key in ("closed", "available", "initialized"))
        or any(
            value[key] is not None and (
                type(value[key]) not in (int, float)
                or not math.isfinite(value[key]) or value[key] < 0
            ) for key in ("last_attempt", "last_duration")
        )
        or (value["last_outcome"] is not None and type(value["last_outcome"]) is not str)
        or type(value["sources"]) is not dict or len(value["sources"]) > 128
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    for name, source in value["sources"].items():
        if (
            type(name) is not str or not name or type(source) is not dict
            or set(source) != {"generation", "updated", "diagnostic"}
            or type(source["generation"]) is not int or source["generation"] < 0
            or (source["updated"] is not None and (
                type(source["updated"]) not in (int, float)
                or not math.isfinite(source["updated"]) or source["updated"] < 0
            ))
            or (source["diagnostic"] is not None and type(source["diagnostic"]) is not str)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
    wire = _encode(value)
    if len(wire) > 65536:
        raise PrivateIPCError(Refusal.SIZE)
    return json.loads(wire)


def capture_refresh(owner: WorktreeRefreshOwner) -> dict:
    if (
        type(owner) not in (WorktreeRefreshOwner, FrozenRefresh)
        or any(task is not None for task in (owner.task, owner._live_task))
        or owner._warm_callbacks
        or (owner._warm_holder is not None and not owner._warm_holder.done())
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    return _validate({
        **{key: getattr(owner, key) for key in _FIELDS},
        "sources": {name: asdict(source) for name, source in owner.sources.items()},
    })


class FrozenRefresh(WorktreeRefreshOwner):
    """Retain age/generation evidence, never a callable refresh owner."""

    def __init__(self, value: object) -> None:
        super().__init__()
        payload = _validate(value)
        for key in _FIELDS:
            setattr(self, key, payload[key])
        self.sources = {
            name: SourceEvidence(**source) for name, source in payload["sources"].items()
        }
        self._wire = _encode(payload)

    def verify(self) -> None:
        if self.token is not None or _encode(capture_refresh(self)) != self._wire:
            raise PrivateIPCError(Refusal.AUTHORITY)

    def sync_sources(self, *args, **kwargs):
        raise PrivateIPCError(Refusal.AUTHORITY)

    def bind(self, *args, **kwargs):
        raise PrivateIPCError(Refusal.AUTHORITY)

    def ensure(self, *args, **kwargs):
        raise PrivateIPCError(Refusal.AUTHORITY)

    def warm(self, *args, **kwargs):
        raise PrivateIPCError(Refusal.AUTHORITY)

    def published(self, *args, **kwargs):
        raise PrivateIPCError(Refusal.AUTHORITY)

    async def close(self) -> None:
        self.verify()
