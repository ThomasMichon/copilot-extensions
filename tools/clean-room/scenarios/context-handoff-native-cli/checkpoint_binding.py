"""Captured native checkpoint identity checks, shared by both sides of rollover."""

import json


def readback_errors(write_row, read_row, clear, owner, *, root_event, object_result, order, after_clear=False):
    errors = []
    for label, row in (("write", write_row), ("read", read_row)):
        if not isinstance(row, dict) or not isinstance(row.get("start"), dict) or not isinstance(row.get("complete"), dict):
            return [f"{label} completion/binding evidence is absent"]
        if not root_event(row["start"]) or not root_event(row["complete"]):
            errors.append(f"{label} is not root-owned")
    written, write_error = object_result(write_row["complete"])
    read, read_error = object_result(read_row["complete"])
    if write_error:
        errors.append(f"write: {write_error}")
    if read_error:
        errors.append(f"read: {read_error}")
    wargs, rargs = write_row["start"]["data"]["arguments"], read_row["start"]["data"]["arguments"]
    if (wargs.get("operation"), wargs.get("path")) != ("write", "checkpoint.md"):
        errors.append("write does not identify checkpoint.md")
    if (rargs.get("operation"), rargs.get("path")) != ("read", "checkpoint.md"):
        errors.append("read does not identify the written checkpoint")
    if after_clear:
        timing = (clear and root_event(clear) and order(write_row["complete"]) < order(clear)
                  < order(read_row["start"]) < order(read_row["complete"]))
    else:
        timing = (clear and root_event(clear) and order(write_row["complete"]) < order(read_row["start"])
                  < order(read_row["complete"]) < order(clear))
    if not timing:
        errors.append("read-back is not bound to the expected side of the root clear")
    if written is not None and read is not None:
        if written.get("operation") not in ("create", "update"):
            errors.append("write result does not acknowledge a captured create/update operation")
        for key in ("agent_session_id", "memory_scope_id", "revision", "source_window"):
            if key not in written or key not in read or written[key] != read[key]:
                errors.append(f"missing or mismatched captured {key}")
        if not isinstance(owner, str) or not owner or written.get("agent_session_id") != owner or read.get("agent_session_id") != owner:
            errors.append("artifact owner differs from root session")
        if not isinstance(written.get("memory_scope_id"), str) or not written["memory_scope_id"]:
            errors.append("artifact memory scope is absent")
        if type(written.get("revision")) is not int or written["revision"] < 1:
            errors.append("write revision is absent or invalid")
        if type(written.get("source_window")) is not int or written["source_window"] < 0:
            errors.append("write source window is absent or invalid")
        if type(read.get("revision")) is not int or type(read.get("source_window")) is not int:
            errors.append("read revision/source window types are invalid")
        if not write_row["start"].get("id") or read.get("history_position") != write_row["start"]["id"]:
            errors.append("read history position does not bind to the write event")
        if (not isinstance(wargs.get("content"), str) or not isinstance(read.get("content"), str)
                or read["content"] != wargs["content"]):
            errors.append("read content differs from written content")
    return errors


def recovered_checkpoint(preclear, clear, read_row, owner, source_window, *,
                         root_event, object_result, check_readback, order):
    starts = [event for event in preclear if event["type"] == "tool.execution_start" and root_event(event)]
    completions = {event["data"]["toolCallId"]: event for event in preclear
                   if event["type"] == "tool.execution_complete" and root_event(event)}
    writes = [event for event in starts if event["data"]["toolName"] == "session_artifacts"
              and event["data"].get("arguments", {}).get("operation") == "write"
              and event["data"]["arguments"].get("path") == "checkpoint.md"]
    if not writes:
        return None, ["no authoritative checkpoint write before the root clear"]
    latest = writes[-1]
    written = {"start": latest, "complete": completions.get(latest["data"]["toolCallId"])}
    if written["complete"] is None:
        return None, ["authoritative checkpoint write completion is missing"]
    reads = [{"start": event, "complete": completions.get(event["data"]["toolCallId"])}
             for event in starts if event["data"]["toolName"] == "session_artifacts"
             and event["data"].get("arguments", {}).get("operation") == "read"
             and event["data"]["arguments"].get("path") == "checkpoint.md"]
    if not any(not check_readback(written, candidate, clear, owner, order=order) for candidate in reads):
        return None, ["authoritative pre-clear checkpoint read-back is not verified"]
    errors = check_readback(written, read_row, clear, owner, order=order, after_clear=True)
    metadata, error = object_result(read_row.get("complete"))
    if error:
        errors.append(f"recovered checkpoint: {error}")
    if metadata is not None:
        if type(metadata.get("source_window")) is not int or metadata["source_window"] != source_window:
            errors.append("recovered checkpoint source window differs from the authoritative root window")
        try:
            document = json.loads(metadata["content"])
        except (KeyError, TypeError, json.JSONDecodeError):
            document = None
        if not isinstance(document, dict):
            errors.append("recovered checkpoint content is not a JSON object")
        elif document.get("session_id") != owner:
            errors.append("recovered checkpoint document differs from the trusted seed session")
    else:
        document = None
    return (None, errors) if errors else (document, [])
