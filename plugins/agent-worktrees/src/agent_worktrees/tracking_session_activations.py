"""Canonical session activation metadata, independent of provider configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass


def activation_token(value: object, *, field: str, limit: int = 128) -> str | None:
    """Validate optional opaque lifecycle metadata without inventing legacy proof."""
    if value is None:
        return None
    if (
        not isinstance(value, str) or not value or len(value) > limit
        or re.fullmatch(r"[A-Za-z0-9._:-]+", value) is None
    ):
        raise ValueError(f"Invalid activation {field}")
    return value


@dataclass
class SessionActivation:
    """One observed interval in which a session was associated with a worktree.

    Hook delivery is at-least-once and a Copilot session may be resumed many
    times. Keeping each interval append-only preserves that history instead of
    overwriting the session's original ``started_at`` on every resume.
    """

    ordinal: int
    started_at: str
    start_recorded_at: str
    start_source: str = "hook"
    ended_at: str | None = None
    end_recorded_at: str | None = None
    end_source: str | None = None
    source_identity: str | None = None
    end_reason: str | None = None
    source_ambiguous: bool = False

    def __post_init__(self) -> None:
        activation_token(self.source_identity, field="source_identity")
        activation_token(self.end_reason, field="end_reason", limit=64)
        if type(self.source_ambiguous) is not bool:
            raise ValueError("Invalid activation source ambiguity")
