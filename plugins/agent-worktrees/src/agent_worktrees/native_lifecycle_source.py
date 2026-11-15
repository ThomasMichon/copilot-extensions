"""Corroborate native lifecycle input without acquiring sessions or authority."""

from __future__ import annotations

import logging
from typing import Any

from . import native_source_context, tracking
from .session_liveness import session_process_owns_hold


def witness(payload: dict[str, Any] | None, *, resident: bool) -> object:
    """Use transported origin metadata; residents must never recapture it."""
    metadata = payload.get("_agentWorktrees") if isinstance(payload, dict) else None
    if isinstance(metadata, dict) and "nativeSource" in metadata:
        return metadata["nativeSource"]
    return None if resident else native_source_context.capture()


def start_identity(session_id: str, source: object) -> str | None:
    """Require the exact UUID's actual open hold in one original ancestor."""
    try:
        matches = [
            token
            for token, pid, birth in native_source_context.candidates(session_id, source)
            if session_process_owns_hold(session_id, pid, birth)
        ]
        if len(matches) != 1:
            raise ValueError("Original native UUID/process binding is unconfirmed")
        return matches[0]
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        logging.getLogger(__name__).warning("Native session start source unconfirmed: %s", exc)
        return None


def retained_start_identity(session_id: str, pid: int | None) -> str | None:
    """Corroborate an external writer's native PID, not its own ancestry."""
    try:
        if type(pid) is not int or pid <= 0:
            raise ValueError("Retained native PID is unavailable")
        observed_epoch = native_source_context.epoch()
        observed = native_source_context.process(pid)
        birth = observed["start_time"]
        token = native_source_context.identity(session_id, pid, birth, observed_epoch)
        if (
            not session_process_owns_hold(session_id, pid, birth)
            or native_source_context.process(pid) != observed
            or native_source_context.epoch() != observed_epoch
        ):
            raise ValueError("Retained native UUID/process binding is unconfirmed")
        return token
    except (OSError, ValueError, TypeError, AttributeError, IndexError, KeyError) as exc:
        logging.getLogger(__name__).warning("Retained native start source unconfirmed: %s", exc)
        return None


def end_identity(
    session_id: str, source: object, entry: tracking.SessionEntry | None,
) -> str | None:
    """Match the captured original source to a unique canonical activation."""
    try:
        tokens = {
            item[0] for item in native_source_context.candidates(session_id, source)
        }
        matches = [
            activation.source_identity
            for activation in entry.activations
            if activation.source_identity in tokens and not activation.source_ambiguous
        ] if entry is not None else []
        if len(matches) != 1:
            raise ValueError("Original native activation is unavailable or ambiguous")
        return matches[0]
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        logging.getLogger(__name__).warning("Native session end source unconfirmed: %s", exc)
        return None
