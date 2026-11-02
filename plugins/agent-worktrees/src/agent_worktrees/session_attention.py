"""Bounded, read-only observations of the root agent's last live-pulse state."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import stat

from . import sessions

CONTRACT = "agent-worktrees.session-attention"
VERSION = 1
MAX_BYTES = 65536
_SESSION_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
_STATES = {"busy", "idle", "awaiting-operator"}


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _reported_at(value):
    if not isinstance(value, str) or not 0 < len(value) <= 64:
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed.astimezone(timezone.utc).isoformat()


def session_attention(session_id: str) -> dict:
    """Read one bounded pulse, never inferring current liveness or pending input."""
    valid_id = (isinstance(session_id, str) and _SESSION_ID.fullmatch(session_id)
                and ".." not in session_id)
    payload = {
        "contract": CONTRACT, "version": VERSION, "supported_versions": [VERSION],
        "capabilities": ["last_reported_root_attention"],
        "session_id": session_id if valid_id else None,
        "availability": "unknown", "as_of": None,
        "source": {"kind": "copilot.substatus.json", "max_bytes": MAX_BYTES},
        "evidence_scope": "last_reported_root_state",
        "runtime_continuity": "unknown",
        "rest": None, "reported_at": None, "reported_at_source": None,
        "issues": {},
    }

    def unknown(reason):
        payload["issues"][reason] = 1
        return payload

    try:
        if not valid_id:
            return unknown("invalid_session_id")
        root = sessions._session_state_dir().resolve()
        candidate = root / session_id / "substatus.json"
        if candidate.resolve() != candidate:
            return unknown("unsafe_session_path")
        initial = candidate.stat()
        if not stat.S_ISREG(initial.st_mode):
            return unknown("not_regular_file")
        if initial.st_size > MAX_BYTES:
            return unknown("oversized_source")
        with candidate.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or _signature(initial) != _signature(opened):
                return unknown("source_changed")
            raw = stream.read(MAX_BYTES + 1)
            final = os.fstat(stream.fileno())
        if len(raw) > MAX_BYTES:
            return unknown("oversized_source")
        if (_signature(opened) != _signature(final)
                or _signature(final) != _signature(candidate.stat())
                or len(raw) != final.st_size):
            return unknown("source_changed")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            return unknown("malformed_source")
        if not isinstance(data, dict):
            return unknown("malformed_source")
        if data.get("sessionId") != session_id:
            return unknown("session_identity_mismatch")
        rest = data.get("rest")
        if not isinstance(rest, str) or rest not in _STATES:
            return unknown("unknown_root_state")
        if "restAt" not in data:
            return unknown("missing_reported_at")
        reported_at = data["restAt"]
        # Initial busy has no transition timestamp; updatedAt times intent, not rest.
        if reported_at is None and rest == "busy":
            timestamp = None
        else:
            try:
                timestamp = _reported_at(reported_at)
            except (ValueError, OverflowError):
                return unknown("invalid_reported_at")
        payload.update(
            availability="observed", rest=rest, reported_at=timestamp,
            reported_at_source="restAt" if timestamp is not None else None,
        )
    except FileNotFoundError:
        return unknown("source_missing")
    except (OSError, RuntimeError, ValueError):
        return unknown("source_unreadable")
    finally:
        payload["as_of"] = datetime.now(timezone.utc).isoformat()
    return payload
