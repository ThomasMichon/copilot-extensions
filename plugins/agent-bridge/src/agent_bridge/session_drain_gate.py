"""Synchronous session drain transitions and their ordinary-mode watchdog."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from .session_manager import log


class _SessionDrainGateMixin:
    @property
    def is_draining(self) -> bool:
        """True once drain() has begun -- new sessions/turns are refused."""
        return self._draining

    def set_draining(
        self, value: bool, *, reason: str | None = None, source: str | None = None,
    ) -> None:
        """Change the ordinary gate and its auto-release watchdog, idempotently."""
        if self._drain_private:
            raise RuntimeError("private drain requires receiver-bound control")
        value = bool(value)
        if value == self._draining:
            return
        self._change_drain_gate(value, reason=reason, source=source)
        if value:
            self._arm_drain_watchdog()
        else:
            self._cancel_drain_watchdog()

    def set_private_draining(self, value: bool) -> None:
        """Change only the gate under a receiver's synchronous routing fence.

        No watchdog may later undrain outside that authority. An existing
        ordinary drain/watchdog must be settled by its owner before this path.
        """
        if (
            type(value) is not bool or self._drain_watchdog is not None
            or (self._draining and not self._drain_private)
        ):
            raise RuntimeError("ordinary drain conflicts with private control")
        self._drain_private = value
        self._change_drain_gate(value, source="private-route-control")

    def _change_drain_gate(
        self, value: bool, *, reason: str | None = None, source: str | None = None,
    ) -> None:
        if value == self._draining:
            return
        self._draining = value
        if value:
            self._draining_since = time.time()
            self._drain_reason = reason
            self._drain_source = source
            log.info(
                "Drain gate OPENED (source=%s reason=%s) -- refusing new "
                "sessions/turns; reads and teardown still served",
                source or "?", reason or "?",
            )
        else:
            held = (
                time.time() - self._draining_since
                if self._draining_since is not None else 0.0
            )
            log.info(
                "Drain gate RELEASED (source=%s) after %.0fs -- accepting new "
                "work", source or "?", held,
            )
            self._draining_since = None
            self._drain_reason = None
            self._drain_source = None

    def drain_status(self) -> dict[str, Any]:
        """Snapshot gate provenance and the ordinary watchdog's auto-release."""
        now = time.time()
        since = self._draining_since
        held = (now - since) if since is not None else None
        auto_at = (
            since + self._drain_auto_release_s
            if since is not None and self._drain_auto_release_s > 0
            and self._drain_watchdog is not None
            and not self._drain_private else None
        )
        return {
            "draining": self._draining,
            "since": (
                datetime.fromtimestamp(since, tz=timezone.utc).isoformat()
                if since is not None else None
            ),
            "held_s": round(held, 1) if held is not None else None,
            "reason": self._drain_reason,
            "source": self._drain_source,
            "auto_release_at": (
                datetime.fromtimestamp(auto_at, tz=timezone.utc).isoformat()
                if auto_at is not None else None
            ),
            "live_host_count": self.live_host_count,
        }

    def _arm_drain_watchdog(self) -> None:
        """Start the bounded-drain watchdog if an event loop is running."""
        self._cancel_drain_watchdog()
        if self._drain_auto_release_s <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._drain_watchdog = loop.create_task(self._drain_watchdog_loop())

    def _cancel_drain_watchdog(self) -> None:
        wd = self._drain_watchdog
        self._drain_watchdog = None
        if wd is not None and not wd.done():
            wd.cancel()

    async def _drain_watchdog_loop(self) -> None:
        """Bound ordinary drains; private controls never arm this task."""
        interval = max(1.0, self._drain_warn_interval_s)
        deadline = (
            (self._draining_since or time.time()) + self._drain_auto_release_s
        )
        try:
            while self._draining:
                await asyncio.sleep(interval)
                if not self._draining:
                    return
                held = (
                    time.time() - self._draining_since
                    if self._draining_since is not None else 0.0
                )
                if time.time() >= deadline:
                    log.warning(
                        "Drain gate open %.0fs (source=%s reason=%s) with no "
                        "handoff completing -- auto-releasing to self-heal (a "
                        "cutover likely aborted)",
                        held, self._drain_source or "?",
                        self._drain_reason or "?",
                    )
                    self.set_draining(False, source="watchdog-auto-release")
                    return
                log.warning(
                    "Still draining after %.0fs (source=%s reason=%s); "
                    "auto-release at %.0fs",
                    held, self._drain_source or "?", self._drain_reason or "?",
                    self._drain_auto_release_s,
                )
        except asyncio.CancelledError:
            return
