"""Shared session lifecycle values, independent of API model families."""

from __future__ import annotations

from enum import Enum


class SessionStatus(str, Enum):
    """Lifecycle states for an agent-bridge session."""

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    IDLE = "idle"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    ENDED = "ended"
