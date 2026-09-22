"""Process-local notifier mixin for queue-adjacent loops."""

from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger("agent-dispatch.queue")


class QueueNotificationMixin:
    """Helpers for post-commit local wake/signal callbacks."""

    def set_wake_notifier(self, notifier: Callable[[], None] | None) -> None:
        self._wake_notifier = notifier

    def set_owned_transition_notifier(
        self, notifier: Callable[[], None] | None
    ) -> None:
        self._owned_transition_notifier = notifier

    def _notify_wake(self) -> None:
        notifier = self._wake_notifier
        if notifier is None:
            return
        try:
            notifier()
        except Exception:
            log.warning("wake notifier failed after durable commit", exc_info=True)

    def _notify_owned_transition(self) -> None:
        notifier = self._owned_transition_notifier
        if notifier is None:
            return
        try:
            notifier()
        except Exception:
            log.warning(
                "owned-transition notifier failed after durable commit",
                exc_info=True,
            )
