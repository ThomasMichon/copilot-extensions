"""Private completed worktree observations, without discovery or maintenance."""

from __future__ import annotations

import asyncio
import json
import math
import threading
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import TypeAdapter
from zdd.private_protocol import PrivateIPCError, Refusal

from .agent_registry_resolver import AgentResolver
from .loop_governance import LoopGovernance
from .resolver_runtime import _encode
from .routes.worktrees import WorktreeDiscoveryCache, _WorktreeEntry, _fresh_start_locks
from .worktree_refresh_runtime import FrozenRefresh, capture_refresh

_MAX_AGENTS = 128
_MAX_ENTRIES = 4096
_MAX_BYTES = 262144


@dataclass
class _Snapshot:
    schema: Literal[1]
    interval: float | int
    entries: dict[str, list[_WorktreeEntry]]
    classify_unsupported: list[str]
    fresh_unsupported: list[str]
    refresh: dict[str, object]


_ADAPTER = TypeAdapter(_Snapshot)


def _decode(payload: object, resolver: AgentResolver) -> _Snapshot:
    try:
        wire = _encode(payload)
        if len(wire) > _MAX_BYTES:
            raise PrivateIPCError(Refusal.SIZE)
        snapshot = _ADAPTER.validate_json(wire, strict=True)
        if (
            type(resolver) is not AgentResolver or type(snapshot.schema) is not int
            or _encode(json.loads(_ADAPTER.dump_json(snapshot))) != wire
            or not math.isfinite(snapshot.interval) or snapshot.interval < 0
            or len(snapshot.entries) > _MAX_AGENTS
            or sum(map(len, snapshot.entries.values())) > _MAX_ENTRIES
            or not set(snapshot.entries) <= set(resolver.agents)
            or snapshot.classify_unsupported != sorted(set(snapshot.classify_unsupported))
            or not set(snapshot.classify_unsupported) <= set(resolver.agents)
            or snapshot.fresh_unsupported != sorted(set(snapshot.fresh_unsupported))
            or not set(snapshot.fresh_unsupported) <= set(resolver.agents)
            or any(
                not resolver.agents[name].project
                or not resolver.agents[name].worktree_discovery
                or len({entry.id for entry in entries}) != len(entries)
                or any(not entry.id or entry.agent_name != name for entry in entries)
                for name, entries in snapshot.entries.items()
            )
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        FrozenRefresh(snapshot.refresh).verify()
        return snapshot
    except PrivateIPCError:
        raise
    except (TypeError, ValueError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


def capture_worktrees(
    cache: WorktreeDiscoveryCache, resolver: AgentResolver,
    governance: LoopGovernance,
) -> dict[str, object]:
    """Copy only completed state on its real owner loop; never crawl to fill it."""
    try:
        if (
            type(cache) is not WorktreeDiscoveryCache
            or type(governance) is not LoopGovernance
            or cache._resolver is not resolver or cache._governance is not governance
            or cache._owner_thread is not threading.current_thread()
            or cache._owner_loop is not asyncio.get_running_loop()
            or cache._refresh.pending
            or any(lock.locked() for lock in _fresh_start_locks.values())
            or any(not task.done() for task in (
                *cache._backfill_tasks, *cache._archive_probe_inflight.values(),
                *cache._live_probe_inflight.values(),
            ))
            or (cache._task is not None and (
                cache._task.done() or cache._cancel_requested
                or cache._task.get_loop() is not cache._owner_loop
            ))
            or (cache._interval > 0 and cache._task is None)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        if (
            len(cache._cache) > _MAX_AGENTS
            or sum(map(len, cache._cache.values())) > _MAX_ENTRIES
        ):
            raise PrivateIPCError(Refusal.SIZE)
        payload = json.loads(_encode({
            "schema": 1, "interval": cache._interval,
            "entries": {
                name: [asdict(entry) for entry in entries]
                for name, entries in cache._cache.items()
            },
            "classify_unsupported": sorted(cache._classify_unsupported),
            "fresh_unsupported": sorted(cache._fresh_unsupported),
            "refresh": capture_refresh(cache._refresh),
        }))
        _decode(payload, resolver)
        return payload
    except PrivateIPCError:
        raise
    except (TypeError, ValueError, AttributeError, RecursionError, RuntimeError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


class FrozenWorktrees(WorktreeDiscoveryCache):
    """Detached completed observations; no refresh, provider probe or task owner."""

    def __init__(self, payload: object, resolver: AgentResolver) -> None:
        snapshot = _decode(payload, resolver)
        super().__init__(snapshot.interval)
        self._wire = _encode(payload)
        self._snapshot_resolver = resolver
        self._cache = snapshot.entries
        self._classify_unsupported = set(snapshot.classify_unsupported)
        self._fresh_unsupported = set(snapshot.fresh_unsupported)
        self._refresh = FrozenRefresh(snapshot.refresh)

    def private_payload(self) -> dict[str, object]:
        """Check the retained graph against its original private bytes."""
        payload = {
            "schema": 1, "interval": self._interval,
            "entries": {
                name: [asdict(entry) for entry in entries]
                for name, entries in self._cache.items()
            },
            "classify_unsupported": sorted(self._classify_unsupported),
            "fresh_unsupported": sorted(self._fresh_unsupported),
            "refresh": capture_refresh(self._refresh),
        }
        if (
            _encode(payload) != self._wire or self._resolver is not None
            or self._governance is not None or self._task is not None
            or type(self._refresh) is not FrozenRefresh or self._backfill_tasks
            or self._archive_probe_inflight or self._live_probe_inflight
            or self._owner_thread is not None or self._owner_loop is not None
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._refresh.verify()
        _decode(payload, self._snapshot_resolver)
        return json.loads(self._wire)

    def get_all(self) -> dict[str, list[_WorktreeEntry]]:
        """Return isolated entries, so a caller cannot mutate retained observations."""
        return _decode(self.private_payload(), self._snapshot_resolver).entries

    async def crawl_if_empty(self) -> None:
        """An actually empty captured cache stays empty; it does not grant a crawl."""
        self.private_payload()

    def render_snapshot(self) -> dict[str, object]:
        self.private_payload()
        return self._refresh.metadata(max_age=max(30.0, self._interval))

    async def refresh_live(self) -> dict[str, object]:
        raise PrivateIPCError(Refusal.AUTHORITY)

    def configure(self, *, interval: float) -> None:
        raise PrivateIPCError(Refusal.AUTHORITY)

    def start(self, resolver: AgentResolver, *, governance: LoopGovernance | None = None) -> None:
        raise PrivateIPCError(Refusal.AUTHORITY)

    async def crawl(self, resolver: AgentResolver) -> None:
        raise PrivateIPCError(Refusal.AUTHORITY)

    async def probe_archived(self, worktree_id: str, resolver: AgentResolver):
        raise PrivateIPCError(Refusal.AUTHORITY)

    async def probe_live(self, worktree_id: str, resolver: AgentResolver):
        raise PrivateIPCError(Refusal.AUTHORITY)

    async def stop(self) -> None:
        self.private_payload()


def restore_worktrees(payload: object, resolver: AgentResolver) -> FrozenWorktrees:
    """Restore the exact completed cache without shelling out or claiming an owner."""
    return FrozenWorktrees(payload, resolver)
