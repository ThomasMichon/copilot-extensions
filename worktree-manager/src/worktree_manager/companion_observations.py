"""Optional metadata observations through the engine's JSON process boundary."""

from __future__ import annotations

import math

from . import engine_client as ec

CONTRACT = "agent-worktrees.session-observations"
VERSION = 1
MAX_RECORDS = 10000


def _count(value) -> bool:
    return type(value) is int and value >= 0 and value.bit_length() <= 256


def _counts(value, keys) -> bool:
    return isinstance(value, dict) and all(_count(value.get(key)) for key in keys)


def _number(value) -> bool:
    return _count(value) or type(value) is float and math.isfinite(value) and value >= 0


def _optional_text(value) -> bool:
    return value is None or isinstance(value, str)


def observation_error(value, session_id: str | None = None) -> str | None:
    """Validate known v1 metadata, ignoring additive fields; never traverse references.

    Collection ceilings bound validation work, not the provider's history contract.
    Returning an error means unknown evidence, never an empty observation.
    """
    if not isinstance(value, dict) or value.get("contract") != CONTRACT:
        return "malformed-observation"
    if type(value.get("version")) is not int:
        return "malformed-observation"
    if value["version"] != VERSION:
        return "unsupported-observation-version"
    versions = value.get("supported_versions")
    if (
        not isinstance(versions, list) or len(versions) > MAX_RECORDS
        or any(type(v) is not int for v in versions) or VERSION not in versions
    ):
        return "malformed-observation"
    sid = value.get("session_id")
    if not isinstance(sid, str) or not sid or (session_id is not None and sid != session_id):
        return "session-mismatch"
    capabilities = value.get("capabilities")
    if isinstance(capabilities, list):
        valid_caps = len(capabilities) <= MAX_RECORDS and all(
            isinstance(item, str) for item in capabilities
        )
    elif isinstance(capabilities, dict):
        valid_caps = len(capabilities) <= MAX_RECORDS and all(
            isinstance(key, str) and type(flag) is bool for key, flag in capabilities.items()
        )
    else:
        valid_caps = False
    availability = value.get("availability")
    as_of = value.get("as_of")
    if (
        not valid_caps or availability not in ("available", "partial", "unavailable")
        or not (isinstance(as_of, str) and bool(as_of) or _number(as_of))
    ):
        return "malformed-observation"
    source, history, issues = (value.get(key) for key in ("source", "history", "issues"))
    if (
        not isinstance(source, dict) or source.get("kind") != "copilot.events.jsonl"
        or "snapshot_bytes" not in source
        or not (source.get("snapshot_bytes") is None or _count(source["snapshot_bytes"]))
        or not _count(source.get("observed_bytes"))
        or not isinstance(history, dict) or history.get("scope") != "retained_file"
        or type(history.get("complete")) is not bool
        or not _counts(history, ("session_start_records", "valid_records"))
        or not isinstance(issues, dict) or len(issues) > MAX_RECORDS
        or not all(isinstance(key, str) and _count(count) for key, count in issues.items())
    ):
        return "malformed-observation"
    tools = value.get("tools")
    if "tools" not in value or "subagents" not in value:
        return "malformed-observation"
    if tools is not None:
        if (
            not _counts(tools, ("started_records", "completed_records", "conflicting_tool_call_ids"))
            or not _counts(tools.get("unattributed_records"), ("started", "completed"))
        ):
            return "malformed-observation"
        names = tools.get("by_name")
        if not isinstance(names, dict) or len(names) > MAX_RECORDS:
            return "malformed-observation"
        if not all(
            isinstance(name, str) and _counts(counts, ("started_records", "completed_records"))
            for name, counts in names.items()
        ):
            return "malformed-observation"
    subagents = value.get("subagents")
    if subagents is not None:
        if (
            not isinstance(subagents, dict)
            or subagents.get("availability") not in ("observed", "unknown")
            or subagents.get("runtime_continuity") != "unknown"
            or not _counts(subagents.get("lifecycle_records"), ("started", "completed", "failed"))
            or not _count(subagents.get("unattributed_records"))
        ):
            return "malformed-observation"
        agents = subagents.get("agents")
        if not isinstance(agents, list) or len(agents) > MAX_RECORDS:
            return "malformed-observation"
        for agent in agents:
            if (
                not isinstance(agent, dict)
                or not isinstance(agent.get("agent_id"), str) or not agent["agent_id"]
                or agent.get("last_reported_lifecycle") not in ("started", "completed", "failed")
                or not _count(agent.get("lifecycle_records"))
                or not all(
                    key in agent and _optional_text(agent[key])
                    for key in ("parent_tool_call_id", "name", "display_name", "model")
                )
                or "reported_duration_ms" not in agent
                or not (agent["reported_duration_ms"] is None or _number(agent["reported_duration_ms"]))
                or "reported_tool_calls" not in agent
                or not (agent["reported_tool_calls"] is None or _count(agent["reported_tool_calls"]))
            ):
                return "malformed-observation"
    pending = value.get("pending_input")
    if not isinstance(pending, dict) or pending.get("availability") != "unknown":
        return "malformed-observation"
    return None


def read_session_observations(
    project: str | None, session_id: str, *, runner=None,
) -> dict:
    """Return ``{observation: envelope | None, error: reason-code | None}``.

    A valid unavailable envelope is retained with ``provider-unavailable``.
    Engine failures do not expose stderr or imply zero agents/calls. Cancellation
    propagates to the caller that owns the read.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        return {"observation": None, "error": "invalid-session-id"}
    try:
        value = ec.run_json(
            project, ["session-observations", session_id, "--json"], runner=runner,
        )
    except ec.EngineError:
        return {"observation": None, "error": "observation-read-failed"}
    error = observation_error(value, session_id)
    if error:
        return {"observation": None, "error": error}
    return {
        "observation": value,
        "error": "provider-unavailable" if value["availability"] == "unavailable" else None,
    }
