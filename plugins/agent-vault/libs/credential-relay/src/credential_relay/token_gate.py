"""Explicit owner of composed relay-token authorization state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .server import ScopeDenied


@dataclass(frozen=True)
class RelayTokenGate:
    """Any-match callbacks with remembered scope denial and static fallback.

    Keeping build-time inputs in one owner avoids opaque closures. Consumers
    may implement private handoff only for callback types they can restore;
    this class does not serialize secrets or infer a callback's provenance.
    """

    validators: tuple[Callable[[str], bool], ...] = field(repr=False)
    authorizers: tuple[Callable[[str, str, dict[str, str]], bool], ...] = field(repr=False)
    static_resources: frozenset[str] = field(repr=False)

    def validate(self, token: str) -> bool:
        """Accept when any registered validator recognizes the token."""
        return any(validator(token) for validator in self.validators)

    def authorize(self, token: str, action: str, fields: dict[str, str]) -> bool:
        """Preserve request-scoped any-match authorization and static fallback."""
        scope_denied = False
        for authorizer in self.authorizers:
            try:
                if authorizer(token, action, fields):
                    return True
            except ScopeDenied:
                scope_denied = True
        if not self.validate(token):
            if scope_denied:
                raise ScopeDenied
            return False
        if action != "get-azure-token":
            return True
        requested = fields.get("scope") or fields.get("resource") or ""
        normalized = requested.removesuffix("/.default").rstrip("/")
        allowed = {
            value.removesuffix("/.default").rstrip("/")
            for value in self.static_resources
        }
        if "*" in self.static_resources or normalized in allowed:
            return True
        raise ScopeDenied
