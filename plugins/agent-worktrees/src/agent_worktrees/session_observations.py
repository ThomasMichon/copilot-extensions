"""Read-only, metadata-only observations of a retained Copilot event file."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
import re

from . import sessions

CONTRACT = "agent-worktrees.session-observations"
VERSION = 1
_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/@+-]{0,255}\Z")
_SESSION_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
_CHUNK_BYTES = 65536
# Oversized records are drained, not silently truncated or mistaken for JSON.
# This bounds transient parsing memory even for a huge tool-output record.
_RECORD_BYTES = 1024 * 1024
_TOOL_TYPES = {"tool.execution_start": "started", "tool.execution_complete": "completed"}
_AGENT_TYPES = {
    "subagent.started": "started",
    "subagent.completed": "completed",
    "subagent.failed": "failed",
}


def _token(value):
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else None


def _label(value):
    if isinstance(value, str) and 0 < len(value) <= 256 and value.isprintable():
        return value
    return None


def _number(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


class _Observations:
    def __init__(self):
        self.records = 0
        self.starts = 0
        self.tools = Counter(started=0, completed=0)
        self.by_name = {"started": Counter(), "completed": Counter()}
        self.unattributed = Counter(started=0, completed=0)
        self.names = {}
        self.conflicting_ids = set()
        self.correlated = {"started": Counter(), "completed": Counter()}
        self.agents = {}
        self.agent_records = Counter(started=0, completed=0, failed=0)
        self.agent_unattributed = 0

    def add(self, event):
        self.records += 1
        kind = event.get("type")
        if kind == "session.start":
            self.starts += 1
        data = event.get("data")
        data = data if isinstance(data, dict) else {}
        if isinstance(kind, str) and kind in _TOOL_TYPES:
            phase = _TOOL_TYPES[kind]
            self.tools[phase] += 1
            name, call_id = _token(data.get("toolName")), _token(data.get("toolCallId"))
            if name:
                self.by_name[phase][name] += 1
                if call_id:
                    if call_id in self.names and self.names[call_id] != name:
                        self.conflicting_ids.add(call_id)
                    self.names[call_id] = name
            elif call_id:
                self.correlated[phase][call_id] += 1
            else:
                self.unattributed[phase] += 1
        elif isinstance(kind, str) and kind in _AGENT_TYPES:
            phase = _AGENT_TYPES[kind]
            self.agent_records[phase] += 1
            agent_id = _token(event.get("agentId"))
            if not agent_id:
                self.agent_unattributed += 1
                return
            agent = self.agents.setdefault(agent_id, {
                "agent_id": agent_id, "last_reported_lifecycle": None,
                "parent_tool_call_id": None,
                "name": None, "display_name": None, "model": None,
                "reported_duration_ms": None, "reported_tool_calls": None,
                "lifecycle_records": 0,
            })
            agent["last_reported_lifecycle"] = phase
            agent["lifecycle_records"] += 1
            parent_call = _token(data.get("toolCallId")) or _token(data.get("parentToolCallId"))
            if parent_call is not None:
                agent["parent_tool_call_id"] = parent_call
            for source, target, validator in (
                ("agentName", "name", _label),
                ("agentDisplayName", "display_name", _label),
                ("model", "model", _label),
                ("durationMs", "reported_duration_ms", _number),
                ("totalToolCalls", "reported_tool_calls", _number),
            ):
                value = validator(data.get(source))
                if value is not None:
                    agent[target] = value

    def result(self):
        for phase, calls in self.correlated.items():
            for call_id, count in calls.items():
                name = self.names.get(call_id)
                if name and call_id not in self.conflicting_ids:
                    self.by_name[phase][name] += count
                else:
                    self.unattributed[phase] += count
        names = sorted(set(self.by_name["started"]) | set(self.by_name["completed"]))
        return {
            "tools": {
                "started_records": self.tools["started"],
                "completed_records": self.tools["completed"],
                "by_name": {
                    name: {f"{phase}_records": self.by_name[phase][name]
                           for phase in ("started", "completed")} for name in names
                },
                "unattributed_records": dict(self.unattributed),
                "conflicting_tool_call_ids": len(self.conflicting_ids),
            },
            "subagents": {
                "availability": "observed" if self.agent_records.total() else "unknown",
                "lifecycle_records": dict(self.agent_records),
                "unattributed_records": self.agent_unattributed,
                "agents": [self.agents[key] for key in sorted(self.agents)],
                "runtime_continuity": "unknown",
            },
        }


def _scan(stream, size, observations, issues):
    """Read at most the initial size; bound a single record's parsing memory."""
    remaining, observed, buffer, oversized = size, 0, bytearray(), False
    while remaining:
        try:
            chunk = stream.read(min(_CHUNK_BYTES, remaining))
        except OSError:
            issues["read_error"] += 1
            break
        if not chunk:
            issues["short_read"] += 1
            break
        remaining -= len(chunk)
        observed += len(chunk)
        pieces = chunk.split(b"\n")
        for index, piece in enumerate(pieces):
            if not oversized:
                if len(buffer) + len(piece) > _RECORD_BYTES:
                    oversized = True
                    buffer.clear()
                    issues["oversized_record"] += 1
                else:
                    buffer.extend(piece)
            if index < len(pieces) - 1:
                if not oversized:
                    try:
                        event = json.loads(buffer)
                        if not isinstance(event, dict):
                            raise ValueError
                    except (ValueError, UnicodeError, RecursionError):
                        issues["malformed_record"] += 1
                    else:
                        observations.add(event)
                buffer.clear()
                oversized = False
    if buffer or oversized:
        issues["partial_final_record"] += 1
    return observed


def _signature(stat):
    signature = stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
    # Windows fstat/path stat disagree on ctime (change versus creation time).
    return signature if os.name == "nt" else (*signature, stat.st_ctime_ns)


def session_observations(session_id: str) -> dict:
    """Return versioned retained-file metadata without writing or exposing messages."""
    payload = {
        "contract": CONTRACT, "version": VERSION,
        "supported_versions": [VERSION], "session_id": session_id,
        "capabilities": ["recorded_tool_events", "reported_subagent_lifecycle"],
        "availability": "unavailable", "as_of": None,
        "source": {"kind": "copilot.events.jsonl", "snapshot_bytes": None,
                   "observed_bytes": 0},
        "history": {"scope": "retained_file", "complete": False,
                    "session_start_records": 0, "valid_records": 0},
        "issues": {}, "tools": None, "subagents": None,
        "pending_input": {"availability": "unknown"},
    }
    observations, issues = _Observations(), Counter()
    try:
        if (not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id)
                or ".." in session_id):
            issues["invalid_session_id"] += 1
            return payload
        root = sessions._session_state_dir()
        candidate = root / session_id / "events.jsonl"
        # Reject directory/file symlink escapes as well as lexical traversal.
        if not candidate.resolve().is_relative_to(root.resolve()):
            issues["unsafe_session_path"] += 1
            return payload
        if sessions.validate_session_id(session_id) is None:
            issues["session_unavailable"] += 1
            return payload
        with candidate.open("rb") as stream:
            initial = os.fstat(stream.fileno())
            payload["source"]["snapshot_bytes"] = initial.st_size
            payload["source"]["observed_bytes"] = _scan(
                stream, initial.st_size, observations, issues,
            )
            final = os.fstat(stream.fileno())
            if _signature(initial) != _signature(final):
                issues["file_changed"] += 1
            if _signature(final) != _signature(candidate.stat()):
                issues["file_replaced"] += 1
        if initial.st_size == 0:
            issues["empty_file"] += 1
    except FileNotFoundError:
        issues["file_missing"] += 1
    except (OSError, RuntimeError, ValueError):
        issues["file_unreadable"] += 1
    finally:
        payload["as_of"] = datetime.now(timezone.utc).isoformat()
        payload["issues"] = dict(issues)
        payload["history"].update(
            session_start_records=observations.starts, valid_records=observations.records,
        )
        if observations.records:
            payload.update(observations.result())
            payload["availability"] = "partial" if issues else "available"
            payload["history"]["complete"] = not issues
    return payload
