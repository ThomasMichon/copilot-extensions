"""Optional last-reported root attention over the selected engine boundary."""

from __future__ import annotations

from datetime import datetime
import re

from rich.text import Text

from . import engine_client as ec

CONTRACT = "agent-worktrees.session-attention"
_SESSION_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
_STATES = {"busy", "idle", "awaiting-operator"}


def _timestamp(value):
    if not isinstance(value, str) or not 0 < len(value) <= 64:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def attention_error(value, session_id: str) -> str | None:
    """Validate known v1 fields; unknown/unsupported is never a legacy fallback."""
    if not isinstance(value, dict) or value.get("contract") != CONTRACT:
        return "malformed-attention"
    if type(value.get("version")) is not int:
        return "malformed-attention"
    if value["version"] != 1:
        return "unsupported-attention-version"
    versions, capabilities = value.get("supported_versions"), value.get("capabilities")
    if (
        not isinstance(versions, list) or len(versions) > 16
        or any(type(version) is not int for version in versions) or 1 not in versions
        or not isinstance(capabilities, list) or len(capabilities) > 64
        or not all(isinstance(item, str) for item in capabilities)
        or "last_reported_root_attention" not in capabilities
        or value.get("session_id") != session_id
        or value.get("evidence_scope") != "last_reported_root_state"
        or value.get("runtime_continuity") != "unknown"
        or not _timestamp(value.get("as_of"))
    ):
        return "malformed-attention"
    source, issues = value.get("source"), value.get("issues")
    if (
        not isinstance(source, dict) or source.get("kind") != "copilot.substatus.json"
        or type(source.get("max_bytes")) is not int or source["max_bytes"] != 65536
        or not isinstance(issues, dict) or len(issues) > 64
        or not all(
            isinstance(key, str) and re.fullmatch(r"[a-z_]{1,64}", key)
            and type(count) is int and 0 < count <= 2**31 - 1
            for key, count in issues.items()
        )
        or not all(key in value for key in ("rest", "reported_at", "reported_at_source"))
    ):
        return "malformed-attention"
    rest, reported_at, timestamp_source = (
        value["rest"], value["reported_at"], value["reported_at_source"])
    if value.get("availability") == "unknown":
        return None if rest is reported_at is timestamp_source is None else "malformed-attention"
    if value.get("availability") != "observed" or not isinstance(rest, str) or rest not in _STATES:
        return "malformed-attention"
    if rest == "busy" and reported_at is timestamp_source is None:
        return None
    if not _timestamp(reported_at) or timestamp_source != "restAt":
        return "malformed-attention"
    return None


def read_session_attention(
    project: str | None, worktree_id: str, session_id: str, *, runner=None,
) -> dict:
    """Revalidate one displayed head, then read only its optional bounded pulse."""
    if (not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id)
            or ".." in session_id or not worktree_id):
        return {"attention": None, "error": "head-session-unavailable"}
    try:
        head = ec.run_json(
            project, ["head-session", "--worktree", worktree_id, "--json"],
            timeout=5, runner=runner,
        )
        # This released read-only baseline predates contract declarations.
        if (
            not isinstance(head, dict) or head.get("worktree_id") != worktree_id
            or head.get("tracked") is not True or head.get("active") is not True
        ):
            return {"attention": None, "error": "head-session-unavailable"}
        if head.get("head_session") != session_id:
            return {"attention": None, "error": "head-changed-refresh-required"}
        value = ec.run_json(
            project, ["session-attention", session_id, "--json"], timeout=5, runner=runner,
        )
    except ec.EngineError:
        return {"attention": None, "error": "attention-read-failed"}
    error = attention_error(value, session_id)
    if error:
        return {"attention": None, "error": error}
    return {
        "attention": value,
        "error": "attention-unavailable" if value["availability"] == "unknown" else None,
    }


def attention_text(value: dict | None, error: str | None) -> Text:
    """Render uncertainty and last-report attribution, never a current-input claim."""
    if not value or value.get("availability") != "observed":
        text = Text("Head CLI attention: unknown", style="#b2b2b2")
        if error == "head-changed-refresh-required":
            text.append(" - head changed; Refresh required", style="bold yellow")
        elif error:
            text.append(f" ({error})")
        return text
    rest = value["rest"]
    color = {"awaiting-operator": "bold yellow", "idle": "#00afaf", "busy": "#b2b2b2"}[rest]
    text = Text(f"Head CLI last reported: {rest}", style=color)
    text.append(f" [{value['session_id'][:12]}]")
    text.append(f"\nTransition: {value['reported_at'] or 'unavailable'}; continuity unknown")
    return text
