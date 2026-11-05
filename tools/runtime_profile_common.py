"""Stdlib-only profile errors and exact preference validation."""
from __future__ import annotations

import re
from typing import Any

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ProfileError(ValueError):
    """A malformed, incompatible, unapproved or ambiguous selection."""


class ProfileUnavailable(ProfileError):
    """No approved record matches the authority's requested selection."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProfileError(f"{label}: expected nonempty trimmed text")
    return value


def _digest(value: Any, label: str) -> str:
    if not _HASH.fullmatch(_text(value, label)):
        raise ProfileError(f"{label}: expected a SHA-256 content identity")
    return value


def validate_profile_preference(preference: tuple[str, ...]) -> None:
    """Validate desired lock identities before any availability-dependent path."""
    for identity in preference:
        _digest(identity, "profile preference")
    if len(set(preference)) != len(preference):
        raise ProfileError("duplicate profile preference")
