"""Owned refresh lifetime and evidence for the worktree memory projection."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from ..agent_registry import AgentResolver

log = logging.getLogger("agent-bridge")

LIVE_WAIT_SECONDS = 10.0
SHUTDOWN_WAIT_SECONDS = 0.1
FRESH_SECONDS = 30.0
RETRY_SECONDS = 10.0
_Row = TypeVar("_Row")


class ObservedRows(list[_Row]):
    """Compatible rows retaining the source's observation time."""

    def __init__(self, rows: list[_Row], observed: float | None) -> None:
        super().__init__(rows)
        self.observed = observed


class DiscoveryReadError(ValueError):
    """A source could not establish a replacement inventory."""


def is_option_unsupported(stderr: str, option: str) -> bool:
    """Inspect rejected arguments, not flags merely printed in argparse usage."""
    for line in (stderr or "").splitlines():
        _, marker, rejected = line.partition("unrecognized arguments")
        if marker and option in rejected.lstrip(":").split():
            return True
    return False


def is_classify_unsupported(stderr: str) -> bool:
    """An older source explicitly rejected the optional classification flag."""
    return is_option_unsupported(stderr, "--classify")


def validate_inventory(raw: str, *, fresh: bool = False) -> float | None:
    """Reject failed/partial/unknown envelopes before replacing good rows."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise DiscoveryReadError("source-malformed") from None
    observed = time.monotonic()
    if isinstance(data, dict):
        version = data.get("version", 1)
        if type(version) is not int:
            raise DiscoveryReadError("source-malformed")
        if version != 1:
            raise DiscoveryReadError("source-incompatible")
        if (
            data.get("error") or data.get("errors") or data.get("incomplete")
            or ("complete" in data and data["complete"] is not True)
            or ("ok" in data and data["ok"] is not True)
        ):
            raise DiscoveryReadError("source-incomplete")
        if data.get("availability") is not None:
            raise DiscoveryReadError("source-degraded")
        if "projection" in data or "contract_version" in data:
            if type(data.get("contract_version")) is not int:
                raise DiscoveryReadError("source-malformed")
            if data["contract_version"] != 1:
                raise DiscoveryReadError("source-incompatible")
            projection = data.get("projection")
            if (
                not isinstance(projection, dict)
                or projection.get("quality") != "current"
            ):
                raise DiscoveryReadError("source-incomplete")
            timestamp = projection.get("observed_at")
            if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
                raise DiscoveryReadError("source-malformed")
            age = time.time() - timestamp
            if timestamp < 0 or age < -1 or age >= FRESH_SECONDS:
                raise DiscoveryReadError("source-stale")
            if fresh:
                # Current-roster-v1 dispatch ignores --fresh; it acknowledges
                # a projection read, not a requested source-owner refresh.
                raise DiscoveryReadError("source-refresh-unsupported")
            observed = time.monotonic() - max(0.0, age)
        data = data.get("worktrees")
    if not isinstance(data, list) or any(
        not isinstance(row, dict)
        or not isinstance(row.get("id") or row.get("name"), str)
        or not (row.get("id") or row.get("name"))
        for row in data
    ):
        raise DiscoveryReadError("source-malformed")
    return observed


@dataclass
class SourceEvidence:
    generation: int = 0
    updated: float | None = None
    diagnostic: str | None = None


class WorktreeRefreshOwner:
    """One retained holder, independent of request and shutdown observation."""

    def __init__(self) -> None:
        self.epoch = 0
        self.generation = 0
        self.token: tuple[Any, ...] | None = None
        self.sources: dict[str, SourceEvidence] = {}
        self.task: asyncio.Task[None] | None = None
        self._live_task: asyncio.Task[None] | None = None
        self._holder_epoch = 0
        self._queued_epoch = 0
        self._queued_live = False
        self._holder_live = False
        self.closed = False
        self.available = False
        self.initialized = False
        self.last_attempt: float | None = None
        self.last_duration: float | None = None
        self.last_outcome: str | None = None
        self._warm_holder: asyncio.Task[None] | None = None

    def sync_sources(
        self, resolver: AgentResolver | None, rows: dict[str, list[_Row]],
        *unsupported: set[str],
    ) -> dict[str, list[_Row]]:
        eligible = [
            (name, cfg) for name, cfg in (resolver.agents if resolver is not None else {}).items()
            if cfg.project and cfg.worktree_discovery
        ]
        token = (id(resolver), tuple(
            (name, cfg.project, cfg.host, cfg.ssh_user, cfg.provider, cfg.ssh_environment)
            for name, cfg in eligible
        ))
        kept = set()
        if self.token is not None and self.token[0] == token[0]:
            previous = {source[0]: source for source in self.token[1]}
            kept = {source[0] for source in token[1] if previous.get(source[0]) == source}
        evidence = {name: value for name, value in self.sources.items() if name in kept}
        if self.bind(token, [name for name, _ in eligible], available=resolver is not None):
            self.sources.update(evidence)
            for verdicts in unsupported:
                verdicts.intersection_update(kept)
            return {name: entries for name, entries in rows.items() if name in kept}
        return rows

    def bind(self, token: tuple[Any, ...], names: list[str], *, available: bool) -> bool:
        if token == self.token:
            return False
        self.token = token
        self.epoch += 1
        self.sources = {name: SourceEvidence() for name in names}
        self.available = available
        self.initialized = False
        self.last_attempt = None
        self.last_duration = None
        self.last_outcome = None
        return True

    @property
    def pending(self) -> bool:
        return any(
            task is not None and not task.done()
            for task in (self.task, self._live_task)
        )

    def current(self, epoch: int) -> bool:
        return not self.closed and epoch == self.epoch

    @property
    def current_pending(self) -> bool:
        return any(
            task is not None and not task.done() and epoch == self.epoch
            for task, epoch in (
                (self.task, self._holder_epoch), (self._live_task, self._queued_epoch),
            )
        )

    def ensure(
        self, operation: Callable[[int], Awaitable[None]], *, live: bool = False,
    ) -> asyncio.Task[None] | None:
        if self.closed or not self.available:
            return None
        if self._live_task is not None and not self._live_task.done():
            previous, epoch, requested_live = (
                self._live_task, self._queued_epoch, self._queued_live,
            )
        else:
            previous, epoch, requested_live = self.task, self._holder_epoch, self._holder_live
        if previous is not None and not previous.done():
            if epoch != self.epoch or (live and not requested_live):
                epoch = self.epoch
                started = time.monotonic()

                async def after_previous_read() -> None:
                    try:
                        await asyncio.shield(previous)
                    except Exception:
                        # The previous holder logs its failure; its successor
                        # still has to establish current source evidence.
                        pass
                    if self.current(epoch):
                        holder = self._admit(operation, epoch=epoch, live=live)
                        await asyncio.shield(holder)

                queued = asyncio.create_task(
                    after_previous_read(), name="worktree-refresh-wait",
                )
                self._live_task = queued
                self._queued_epoch = epoch
                self._queued_live = live
                self._observe(queued, epoch=epoch, started=started)
                return queued
            return previous
        return self._admit(operation, epoch=self.epoch, live=live)

    def _admit(
        self, operation: Callable[[int], Awaitable[None]], *, epoch: int, live: bool,
    ) -> asyncio.Task[None]:
        started = time.monotonic()
        self.last_attempt = started
        task = asyncio.create_task(operation(epoch), name="worktree-discovery-refresh")
        self.task = task
        self._holder_epoch = epoch
        self._holder_live = live
        self._observe(task, epoch=epoch, started=started)
        return task

    def _observe(self, task: asyncio.Task[None], *, epoch: int, started: float) -> None:
        def finished(holder: asyncio.Task[None]) -> None:
            # Retrieve failures even if every request abandoned its wait.
            error = None if holder.cancelled() else holder.exception()
            if error is not None:
                log.error("Worktree refresh failed", exc_info=(
                    type(error), error, error.__traceback__,
                ))
            if self.current(epoch):
                self.last_duration = time.monotonic() - started
                if holder.cancelled() or error is not None:
                    for source in self.sources.values():
                        source.diagnostic = "refresh-cancelled" if holder.cancelled() else "refresh-failed"
                    self.last_outcome = "failed"
                else:
                    self.last_outcome = (
                        "partial" if any(s.diagnostic for s in self.sources.values())
                        else "complete"
                    )
            # Only actual task completion permits the next admission.
            if self.task is holder:
                self.task = None
            if self._live_task is holder:
                self._live_task = None

        task.add_done_callback(finished)

    def needs_refresh(self, *, max_age: float = FRESH_SECONDS) -> bool:
        if self.current_pending or self.closed or not self.available:
            return False
        now = time.monotonic()
        if self.last_attempt is not None and now - self.last_attempt < RETRY_SECONDS:
            return False
        return not self.initialized or any(
            source.updated is None or source.diagnostic is not None
            or now - source.updated >= max_age
            for source in self.sources.values()
        )

    def warm(
        self, operation: Callable[[int], Awaitable[None]],
        backfill: Callable[[], Awaitable[None]], wrappers: set[asyncio.Task[None]],
    ) -> asyncio.Task[None] | None:
        joined = self.current_pending
        task = self.ensure(operation)
        if task is not None and not joined and task is not self._warm_holder:
            self._warm_holder = task
            epoch = self.epoch

            def schedule(holder: asyncio.Task[None]) -> None:
                if (
                    not self.current(epoch) or holder.cancelled()
                    or holder.exception() is not None
                ):
                    return
                pending = asyncio.create_task(backfill(), name="worktree-classify-backfill")
                wrappers.add(pending)
                pending.add_done_callback(wrappers.discard)

            task.add_done_callback(schedule)
        return task

    def published(
        self, epoch: int, errors: dict[str, str | None],
        *, observed: dict[str, float] | None = None,
    ) -> None:
        if not self.current(epoch):
            return
        self.generation += 1
        now = time.monotonic()
        for name, error in errors.items():
            source = self.sources[name]
            source.diagnostic = error
            if error is None:
                source.generation = self.generation
                source.updated = (observed or {}).get(name, now)
        self.initialized = True

    async def wait(self, task: asyncio.Task[None] | None) -> dict[str, Any]:
        started = time.monotonic()
        epoch = self.epoch
        outcome = "unavailable"
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=LIVE_WAIT_SECONDS)
                outcome = (
                    "superseded" if epoch != self.epoch or self.closed
                    else self.last_outcome or "superseded"
                )
            except asyncio.TimeoutError:
                outcome = "failed" if task.done() else "timed-out"
            except Exception:
                # The holder callback logs the actual failure; this is wire evidence.
                outcome = "failed"
        return {
            "outcome": outcome,
            "wait_seconds": time.monotonic() - started,
            "wait_limit_seconds": LIVE_WAIT_SECONDS,
            "pending": self.pending,
        }

    async def close(self) -> None:
        self.closed = True
        self.epoch += 1
        if self.pending:
            # Observation expiry neither cancels nor releases the holder.
            await asyncio.wait({
                task for task in (self.task, self._live_task)
                if task is not None and not task.done()
            }, timeout=SHUTDOWN_WAIT_SECONDS)

    def metadata(self, *, max_age: float = FRESH_SECONDS) -> dict[str, Any]:
        now = time.monotonic()
        sources = {}
        for name, source in self.sources.items():
            age = None if source.updated is None else max(0.0, now - source.updated)
            sources[name] = {
                "generation": source.generation,
                "age_seconds": age,
                "freshness": (
                    "unknown" if age is None
                    else "stale" if source.diagnostic or age >= max_age or self.closed
                    else "fresh"
                ),
                "completeness": (
                    "unknown" if age is None and source.diagnostic is None
                    else "incomplete" if source.diagnostic else "complete"
                ),
                "diagnostic": source.diagnostic,
            }
        complete = (
            self.available and self.initialized and not self.closed
            and all(s["completeness"] == "complete" for s in sources.values())
        )
        freshness = (
            "unavailable" if not self.available or self.closed
            else "unknown" if not self.initialized
            else "fresh" if complete and all(s["freshness"] == "fresh" for s in sources.values())
            else "stale"
        )
        return {
            "version": 1,
            "epoch": self.epoch,
            "generation": self.generation,
            "freshness": freshness,
            "completeness": (
                "complete" if complete else "incomplete" if self.initialized else "unknown"
            ),
            "refresh_pending": self.pending,
            "warming": self.pending and not self.initialized,
            "sources": sources,
            "last_refresh": {
                "outcome": self.last_outcome,
                "duration_seconds": self.last_duration,
                "goal_seconds": LIVE_WAIT_SECONDS,
                "goal_met": (
                    None if self.pending or self.last_duration is None else
                    self.last_outcome == "complete" and self.last_duration <= LIVE_WAIT_SECONDS
                ),
            },
        }
