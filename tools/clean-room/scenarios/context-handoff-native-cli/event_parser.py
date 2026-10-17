"""Shared captured-event row and owner-scoped tool-call relationship validation."""

import json


def event_order(events):
    positions = {id(event): position for position, event in enumerate(events)}
    return lambda event: positions[id(event)]


def call_owner(event):
    top, nested = event.get("agentId"), event["data"].get("agentId")
    if top is None and nested is None:
        return ("root", "")
    markers = [value for value in (top, nested) if value is not None]
    if all(isinstance(value, str) and value for value in markers) and len(set(markers)) == 1:
        return ("agent", markers[0])
    # Non-null opaque markers never become root or collide with string owners.
    return ("opaque-nonroot", json.dumps([top, nested], sort_keys=True))


def call_relationship_errors(rows):
    errors, starts, completed = [], {}, set()
    for line, event in rows:
        kind = event["type"]
        if kind not in ("tool.execution_start", "tool.execution_complete"):
            continue
        data = event["data"]
        key = (call_owner(event), data["toolCallId"])
        if kind == "tool.execution_start":
            if key in starts:
                errors.append({"source": "events", "line": line, "error": "duplicate tool start in the same owner scope"})
            else:
                starts[key] = event
        else:
            if key in completed:
                errors.append({"source": "events", "line": line, "error": "duplicate tool completion in the same owner scope"})
            completed.add(key)
            start = starts.get(key)
            if start is None:
                errors.append({"source": "events", "line": line, "error": "tool completion lacks a prior same-owner start"})
            elif event["timestamp"] < start["timestamp"]:
                errors.append({"source": "events", "line": line, "error": "tool completion timestamp precedes its start"})
    # An unfinished start is valid at a bounded final capture.
    return errors


def event_stream(path):
    errors, events, rows = [], [], []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return [], [{"source": "events", "error": "event stream unavailable or unreadable"}]
    if not lines:
        errors.append({"source": "events", "error": "event stream is empty; no run evidence"})
    for number, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            errors.append({"source": "events", "line": number, "error": "invalid event JSON"})
            continue
        if (not isinstance(event, dict) or not isinstance(event.get("data"), dict)
                or any(not isinstance(event.get(key), str) or not event[key].strip()
                       for key in ("id", "type", "timestamp"))):
            errors.append({"source": "events", "line": number, "error": "invalid event object"})
            continue
        model = event["data"].get("model")
        if model is not None and not isinstance(model, str):
            errors.append({"source": "events", "line": number, "error": "invalid optional model metadata"})
            continue
        if event["type"] in ("tool.execution_start", "tool.execution_complete"):
            data = event["data"]
            if (not isinstance(data.get("toolCallId"), str) or not data["toolCallId"].strip()
                    or (event["type"] == "tool.execution_start" and (
                        not isinstance(data.get("toolName"), str) or not data["toolName"].strip()
                        or not isinstance(data.get("arguments"), dict)))):
                errors.append({"source": "events", "line": number, "error": "invalid tool event metadata"})
                continue
            if (event["type"] == "tool.execution_start" and data["toolName"] in ("bash", "shell")
                    and not isinstance(data["arguments"].get("command"), str)):
                errors.append({"source": "events", "line": number, "error": "invalid shell command metadata"})
                continue
        if event["type"] == "user.message" and not isinstance(event["data"].get("content"), str):
            errors.append({"source": "events", "line": number, "error": "invalid user message content"})
            continue
        if event["type"] == "skill.context_delivered_ref" and any(
                not isinstance(event["data"].get(key), str) or not event["data"][key].strip()
                for key in ("source", "prefix", "interactionId")):
            errors.append({"source": "events", "line": number, "error": "invalid skill delivery metadata"})
            continue
        events.append(event)
        rows.append((number, event))
    errors.extend(call_relationship_errors(rows))
    return events, errors
