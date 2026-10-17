"""Private transfer of a warmed roster without discovery or provider execution."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import TypeAdapter
from zdd.private_protocol import PrivateIPCError, Refusal

from .agent_registry_cache import AgentRosterCache, AgentRosterSnapshot, _NamespaceState
from .agent_registry_resolver import AgentResolver
from .resolver_runtime import _encode


@dataclass
class _Entry:
    state: Literal["ok", "failed"]
    rows: list[dict[str, object]]
    refreshed_at: float | int | None


@dataclass
class _Roster:
    schema: Literal[1]
    freshness_seconds: float | int
    discovery_at: float | int
    entries: dict[str, _Entry]


_ADAPTER = TypeAdapter(_Roster)


def _decode(payload: object, resolver: AgentResolver) -> _Roster:
    try:
        wire = _encode(payload)
        state = _ADAPTER.validate_json(wire, strict=True)
        now = time.monotonic()
        if (
            type(resolver) is not AgentResolver
            or type(state.schema) is not int
            or _encode(json.loads(_ADAPTER.dump_json(state))) != wire
            or not 0 < state.freshness_seconds
            or not 0 <= state.discovery_at <= now
            or set(state.entries) != set(resolver.namespace_resolvers)
            or any(
                (entry.state == "ok" and entry.refreshed_at is None)
                or (entry.refreshed_at is not None and not 0 <= entry.refreshed_at <= now)
                for entry in state.entries.values()
            )
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        return state
    except PrivateIPCError:
        raise
    except (TypeError, ValueError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


def capture_roster(cache: AgentRosterCache, resolver: AgentResolver) -> dict[str, object]:
    """Capture completed native-clock observations, never an unscanned roster."""
    try:
        if (
            type(cache) is not AgentRosterCache or cache.resolver is not resolver
            or not cache.discovery_ever_ok or cache._discovery_ok_at is None
            or cache._stopping or cache._cycle_started_at is not None
            or (cache._discovery_task is not None and not cache._discovery_task.done())
            or any(not task.done() for task in (
                *cache._inflight.values(), *cache._owned_scans, *cache._owned_provider_reads,
            ))
            or set(cache._entries) != set(resolver.namespace_resolvers)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        entries = {}
        for prefix, entry in cache._entries.items():
            if (
                entry.provider_ref is not resolver.namespace_resolvers[prefix]
                or entry.state not in (_NamespaceState.OK, _NamespaceState.FAILED)
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            entries[prefix] = {
                "state": entry.state.value, "rows": entry.rows,
                "refreshed_at": entry.last_refreshed_monotonic,
            }
        payload = json.loads(_encode({
            "schema": 1, "freshness_seconds": cache._freshness_deadline,
            "discovery_at": cache._discovery_ok_at, "entries": entries,
        }))
        _decode(payload, resolver)
        return payload
    except PrivateIPCError:
        raise
    except (TypeError, ValueError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


@dataclass(frozen=True)
class FrozenRoster:
    """Read-only pre-adoption roster; no scan, task or provider refresh is armed.

    Same-execution-space monotonic timestamps retain their original age.
    Explicit refresh and expired observations never report completeness.
    A post-exit lifecycle must replace this with a supervised live cache.
    """

    resolver: AgentResolver = field(repr=False)
    _wire: bytes = field(repr=False)

    def private_payload(self) -> dict[str, object]:
        payload = json.loads(self._wire)
        _decode(payload, self.resolver)
        return payload

    def ready(self) -> bool:
        state = _decode(json.loads(self._wire), self.resolver)
        return time.monotonic() - state.discovery_at <= state.freshness_seconds

    async def get_snapshot(self, *, force_refresh: bool = False) -> AgentRosterSnapshot:
        """Serve only still-fresh rows; a requested rescan stays visibly incomplete."""
        state = _decode(json.loads(self._wire), self.resolver)
        now = time.monotonic()
        rows = self.resolver.list_agents()
        incomplete = []
        for prefix, entry in state.entries.items():
            if (
                not force_refresh and entry.state == "ok"
                and entry.refreshed_at is not None
                and now - entry.refreshed_at <= state.freshness_seconds
            ):
                rows.extend(entry.rows)
            else:
                incomplete.append(prefix)
        return AgentRosterSnapshot(
            rows, sorted(incomplete),
            not force_refresh and not incomplete
            and now - state.discovery_at <= state.freshness_seconds,
        )


def restore_roster(payload: object, resolver: AgentResolver) -> FrozenRoster:
    """Validate a detached snapshot without creating a live cache or refreshing it."""
    _decode(payload, resolver)
    return FrozenRoster(resolver, _encode(payload))
