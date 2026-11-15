"""Coalesced unlock-triggered cache work owned by the serving generation."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import contextlib
from dataclasses import dataclass, field, replace
import threading
import time
from typing import TYPE_CHECKING

from . import cache_sources, config
from .cache import cache_enabled
from .cache_warm_status import WarmState
from .credential_fields import canonical_field
from .extensions import get_registry
from .service_cache import ReadAdmissionExpired, get_cache
from .service_cache_store import ServiceCacheStore
from .service_connections import settle_owned

if TYPE_CHECKING:
    from .service import VaultService
    from .service_connections import VaultConnections

MAX_CONTEXTS = 32
MAX_TARGETS = 256
MAX_ITEMS = 1024
MAX_SOURCES = 32
WARM_SECONDS = 60.0


@dataclass
class WarmRequest:
    kpdb: str
    group: str
    stamp: float
    token: object
    deadline: float
    sources: tuple[cache_sources.CacheSource, ...]
    progress: WarmState = field(default_factory=lambda: WarmState("queued"))


class CacheWarmQueue:
    def __init__(self, service: VaultService) -> None:
        self.service = service
        self.closed = False
        self._lock = threading.Lock()
        self._foreground = threading.Condition()
        self._foreground_count = 0
        self._records: OrderedDict[tuple[str, str], WarmRequest] = OrderedDict()
        self._pending: OrderedDict[tuple[str, str], WarmRequest] = OrderedDict()
        self._epochs: dict[str, object] = {}
        self._default_groups: dict[str, str] = {}
        self._refused: tuple[tuple[str, str], WarmState] | None = None

    @contextlib.contextmanager
    def foreground(self) -> Iterator[None]:
        with self._foreground:
            self._foreground_count += 1
        try:
            yield
        finally:
            with self._foreground:
                self._foreground_count -= 1
                self._foreground.notify_all()

    def wait_for_foreground(self, deadline: float) -> bool:
        with self._foreground:
            while self._foreground_count and not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._foreground.wait(remaining)
            return not self.closed

    def foreground_pending(self) -> bool:
        with self._foreground:
            return self._foreground_count > 0

    def enqueue(self, kpdb: str, group: str | None, *, confirmed: bool = False) -> WarmState:
        with self._lock:
            if self.closed:
                return WarmState("not-queued", "service-closing")
            if confirmed and kpdb in self._epochs:
                self._epochs[kpdb] = object()
            if confirmed and group is None:
                self._default_groups.pop(kpdb, None)
        if not cache_enabled():
            return WarmState("disabled")
        selected_group = group
        try:
            selected_group = group if group is not None else config.load_config().group or ""
            sources = tuple(
                source.fn for source in get_registry().cache_sources[: MAX_SOURCES + 1]
            )
        except Exception:
            state = WarmState("not-queued", "configuration-unavailable")
            if selected_group is not None:
                with self._lock:
                    self._refused = (kpdb, selected_group), state
            return replace(state)
        if not sources:
            state = WarmState("disabled", "no-cache-sources")
            with self._lock:
                self._refused = (kpdb, selected_group), state
            return replace(state)
        key = kpdb, selected_group
        stamp = self.service._password_set_at.get(kpdb)
        if stamp is None:
            return WarmState("not-queued", "unknown-unlock-generation")
        with self._lock:
            if self.closed:
                return WarmState("not-queued", "service-closing")
            token = self._epochs.get(kpdb)
            current = self._records.get(key)
            if current is not None and current.stamp == stamp and current.token is token:
                if group is None:
                    self._default_groups[kpdb] = selected_group
                return replace(current.progress)
            if current is None and len(self._records) >= MAX_CONTEXTS:
                reusable = next(
                    (
                        context
                        for context, request in self._records.items()
                        if request.progress.state not in {"queued", "running"}
                    ),
                    None,
                )
                if reusable is None:
                    state = WarmState("not-queued", "capacity")
                    self._refused = key, state
                    return replace(state)
                previous = self._records.pop(reusable)
                if not any(request.kpdb == previous.kpdb for request in self._records.values()):
                    self._epochs.pop(previous.kpdb, None)
                    self._default_groups.pop(previous.kpdb, None)
            if token is None:
                token = object()
            self._epochs[kpdb] = token
            request = WarmRequest(
                kpdb,
                selected_group,
                stamp,
                token,
                time.monotonic() + WARM_SECONDS,
                sources,
            )
            self._records[key] = self._pending[key] = request
            if group is None:
                self._default_groups[kpdb] = selected_group
            self._records.move_to_end(key)
            if self._refused is not None and self._refused[0] == key:
                self._refused = None
            return replace(request.progress)

    def take(self) -> WarmRequest | None:
        with self._lock:
            if self.closed or not self._pending:
                return None
            _key, request = self._pending.popitem(last=False)
            request.progress.state = "running"
            return request

    def is_current(self, request: WarmRequest) -> bool:
        with self._lock:
            return self._epochs.get(request.kpdb) is request.token

    def update(self, request: WarmRequest, state: WarmState) -> None:
        with self._lock:
            request.progress = replace(state)
            if state.state not in {"queued", "running"}:
                request.sources = ()

    def status(self, kpdb: str | None, group: str | None) -> dict[str, object]:
        if not cache_enabled():
            return WarmState("disabled").describe()
        if kpdb is None:
            return WarmState("idle").describe()
        with self._lock:
            selected_group = group if group is not None else self._default_groups.get(kpdb)
            if selected_group is None:
                return WarmState("idle", "context-unavailable").describe()
            key = kpdb, selected_group
            if self._refused is not None and self._refused[0] == key:
                return self._refused[1].describe()
            request = self._records.get(key)
            if request is not None and self._epochs.get(kpdb) is not request.token:
                return WarmState("not-queued", "unlock-generation-changed").describe()
            return (request.progress if request is not None else WarmState("idle")).describe()

    def close(self) -> None:
        with self._lock:
            self.closed = True
            for request in self._pending.values():
                request.progress = WarmState("cancelled", "service-closing")
                request.sources = ()
            self._pending.clear()
        with self._foreground:
            self._foreground.notify_all()


class CacheWarmRuntime:
    def __init__(
        self,
        service: VaultService,
        queue: CacheWarmQueue,
        connections: VaultConnections | None,
    ) -> None:
        self.service, self.queue, self.connections = service, queue, connections
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vault-cache-warm")
        self.future: asyncio.Future[None] | None = None

    def tick(self) -> None:
        if (
            self.queue.closed
            or self.service._shutdown
            or not self.service._runtime_can_work
            or self.connections is not None
            and self.connections.closing
            or self.future is not None
            and not self.future.done()
        ):
            return
        request = self.queue.take()
        if request is None:
            return
        self.future = asyncio.get_running_loop().run_in_executor(self.executor, self._run, request)
        if self.connections is not None:
            for owned in (self.connections.work, self.connections.workers):
                owned.add(self.future)
                self.future.add_done_callback(owned.discard)

    def _stopped(self, request: WarmRequest) -> str:
        if (
            self.queue.closed
            or self.service._shutdown
            or not self.service._runtime_can_work
            or self.connections is not None
            and self.connections.closing
        ):
            return "service-closing"
        if time.monotonic() >= request.deadline:
            return "deadline"
        if not cache_enabled():
            return "cache-disabled"
        if (
            not self.queue.is_current(request)
            or self.service._password_set_at.get(request.kpdb) != request.stamp
        ):
            return "unlock-generation-changed"
        return ""

    def _acquire_custody(self, request: WarmRequest) -> bool:
        deadline = min(request.deadline, time.monotonic() + 2.0)
        while self.queue.wait_for_foreground(deadline):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._stopped(request):
                return False
            if not self.service._credential_lock.acquire(timeout=remaining):
                return False
            if not self.queue.foreground_pending():
                return True
            self.service._credential_lock.release()
        return False

    def _run(self, request: WarmRequest) -> None:
        progress = WarmState("running")
        self.queue.update(request, progress)
        self.service._request_ctx.require_existing_unlock = True
        self.service._request_ctx.cache_warm_deadline = request.deadline
        try:
            reason = self._stopped(request)
            if reason:
                progress.state, progress.reason = "partial", reason
                return
            persistent = get_cache()
            if not persistent.enabled:
                progress.state, progress.reason = "failed", "cache-unavailable"
                return
            targets = cache_sources.collect(
                request.sources,
                None,
                max_entries=MAX_TARGETS,
                max_items=MAX_ITEMS,
                max_sources=MAX_SOURCES,
                deadline=request.deadline,
            )
            entries: list[tuple[str, str]] = []
            seen: set[tuple[str, str]] = set()
            for entry, field_name in targets.entries:
                field_name = canonical_field(field_name)
                identity = config.normalize_entry(entry, request.group), field_name
                if identity not in seen:
                    seen.add(identity)
                    entries.append((entry, field_name))
            progress.selected = len(entries)
            progress.failed_sources, progress.truncated = targets.failed_sources, targets.truncated
            self.queue.update(request, progress)
            self.service._request_ctx.allow_prompt = False
            self.service._request_ctx.group = request.group
            self.service._request_ctx.vault_name = ""
            self.service._request_ctx.reason = "configured cache warming"
            for entry, field_name in entries:
                reason = self._stopped(request)
                if reason:
                    progress.reason = reason
                    break
                if not self._acquire_custody(request):
                    progress.reason = self._stopped(request) or "credential-custody-busy"
                    break
                try:
                    self.service._check_password_ttl()
                    reason = self._stopped(request)
                    if reason or not self.service.cli.has_password(request.kpdb):
                        progress.reason = reason or "locked"
                        break
                    value, generation = self.service.get_with_generation(
                        request.kpdb,
                        entry,
                        field_name,
                        request.group,
                        refresh=True,
                    )
                    record = ServiceCacheStore(persistent, request.kpdb, request.group).record(
                        entry,
                        field_name,
                    )
                    if (
                        value is not None
                        and record is not None
                        and record["value"] == value
                        and record["generation"] == generation
                    ):
                        progress.cached += 1
                    else:
                        progress.failed += 1
                    self.queue.update(request, progress)
                except ReadAdmissionExpired:
                    progress.reason = "deadline"
                    break
                except Exception:
                    progress.failed += 1
                    self.queue.update(request, progress)
                finally:
                    self.service._credential_lock.release()
            progress.state = (
                "partial"
                if progress.reason
                or progress.failed
                or progress.failed_sources
                or progress.truncated
                else "complete"
            )
        except Exception:
            progress.state, progress.reason = "failed", "cache-unavailable"
        finally:
            self.service._request_ctx.require_existing_unlock = False
            self.service._request_ctx.cache_warm_deadline = None
            self.queue.update(request, progress)

    async def close(self) -> None:
        self.queue.close()
        try:
            if self.future is not None:
                await settle_owned(self.future)
        finally:
            self.executor.shutdown(wait=True)
