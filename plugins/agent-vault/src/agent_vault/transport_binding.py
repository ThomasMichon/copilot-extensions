"""Single-attempt mutation routing selected by the existing health transport."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

Sender = Callable[[dict[str, Any], float | None], dict[str, Any] | None]


class MutationBinding:
    """Keep a health-selected connector; never rediscover after a mutation."""

    def __init__(self, operation: Literal["import", "export"] = "import") -> None:
        self._owner_field = "key_attachment_" + operation + "_owner"
        self._expected_field = "expected_" + operation + "_owner"
        self._sender: Sender | None = None
        self._owner: str | None = None
        self._used = False
        self._selected = False
        self._defaults: dict[str, Any] = {}

    def require_unselected(self) -> None:
        if self._selected:
            raise ValueError("Transport binding already selected")

    def select(
        self,
        sender: Sender,
        *,
        single_attempt: bool,
        defaults: dict[str, Any],
    ) -> None:
        self.require_unselected()
        self._selected = True
        if single_attempt:
            self._sender = sender
            self._defaults = dict(defaults)

    def accept_health(self, response: dict[str, Any]) -> None:
        owner = response.get(self._owner_field)
        if isinstance(owner, str) and owner and self._sender is not None:
            self._owner = owner

    def matches_context(self, context: dict[str, Any]) -> bool:
        return self._selected and self._defaults == context

    @property
    def ready(self) -> bool:
        return self._sender is not None and self._owner is not None and not self._used

    def send_once(
        self, request: dict[str, Any], timeout: float | None
    ) -> dict[str, Any] | None:
        if not self.ready or self._sender is None:
            raise ValueError("No unused mutation-capable transport binding")
        if {"kpdb", "group", "vault"} & request.keys():
            raise ValueError("Mutation cannot override the health-selected context")
        self._used = True
        outgoing = dict(self._defaults)
        outgoing.update(request)
        outgoing[self._expected_field] = self._owner
        try:
            result = self._sender(outgoing, timeout)
        except Exception:
            return None
        if not isinstance(result, dict) or result.get(self._owner_field) != self._owner:
            return None
        return result
