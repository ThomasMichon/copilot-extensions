"""Synthetic task validation, not a native artifact writer or Tier-E judge."""

import json
from pathlib import Path
import re
import shlex
import sys

RECOVERY_PREREQUISITES = [
    "read_bound_checkpoint",
    "read_AGENTS.md",
    "refresh_context-handoff_skill",
    "get_context_remaining",
]


def result_text(completion):
    result = completion.get("data", {}).get("result")
    return result.get("content") if isinstance(result, dict) else None


def successful(completion):
    if completion is None or not isinstance(completion.get("data"), dict) or completion["data"].get("success") is not True:
        return False
    data = completion["data"]
    exit_code = data.get("shellExecution", {}).get("exitCode")
    telemetry_code = data.get("toolTelemetry", {}).get("metrics", {}).get("exit_code")
    return exit_code in (None, 0) and telemetry_code in (None, 0)


def root_event(event):
    data = event.get("data", {})
    return not event.get("agentId") and not (isinstance(data, dict) and data.get("agentId"))


def object_result(completion):
    if not successful(completion):
        return None, "tool execution unsuccessful"
    try:
        payload = json.loads(result_text(completion))
    except (json.JSONDecodeError, TypeError):
        return None, "tool result is not valid JSON"
    if not isinstance(payload, dict):
        return None, "tool result is not an object"
    return payload, None


def measurement_result(completion):
    payload, error = object_result(completion)
    if error:
        return None, error
    window = payload.get("context_window")
    info = payload.get("context_info")
    number = window.get("number") if isinstance(window, dict) else None
    limit = info.get("promptTokenLimit") if isinstance(info, dict) else None
    left, fraction = payload.get("tokens_left"), payload.get("fraction_remaining")
    if (type(number) is not int or number < 1
            or not isinstance(window.get("current_window_id"), str)
            or not window["current_window_id"]
            or type(limit) not in (int, float) or limit <= 0
            or type(left) not in (int, float) or left < 0
            or type(fraction) not in (int, float) or not 0 <= fraction <= 1):
        return None, "native measurement lacks valid captured window/token fields"
    return payload, None


def checkpoint_readback_errors(write_row, read_row, clear, owner):
    """Use captured path, owner/scope, revision, source window and history binding."""
    errors = []
    for label, row in (("write", write_row), ("read", read_row)):
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
    if not (clear and root_event(clear)
            and write_row["complete"]["timestamp"] < read_row["start"]["timestamp"]
            and read_row["complete"]["timestamp"] < clear["timestamp"]):
        errors.append("read-back is not between the successful write and root clear")
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


def normalized_content(text):
    return re.sub(r"(?m)^L\d+:", "", text.replace("\r\n", "\n")).strip()


def resolved_read_paths(start, workspace):
    """Attribute read output to actual file operands, never a quoted title."""
    name = start["data"]["toolName"]
    args = start["data"].get("arguments", {})
    if name == "view" and isinstance(args.get("path"), str):
        path = Path(args["path"])
        return [str(path if path.is_absolute() else Path(workspace) / path)]
    if name not in ("bash", "shell"):
        return []
    try:
        lexer = shlex.shlex(args.get("command", ""), posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    cwd = Path(workspace)
    paths = []
    index = 0
    command_start = True
    while index < len(tokens):
        token = tokens[index]
        if token in (";", "&&", "||", "|"):
            command_start = True
            index += 1
            continue
        if command_start and token == "cd" and index + 1 < len(tokens):
            operand = Path(tokens[index + 1])
            cwd = operand if operand.is_absolute() else cwd / operand
        if command_start and token in ("cat", "head", "tail", "sed"):
            end = index + 1
            while end < len(tokens) and tokens[end] not in (";", "&&", "||", "|"):
                operand = tokens[end]
                if not operand.startswith("-") and ("/" in operand or operand.endswith(".md") or operand.endswith(".txt")):
                    path = Path(operand)
                    paths.append(str(path if path.is_absolute() else cwd / path))
                end += 1
        command_start = False
        index += 1
    return paths


def content_read(start, completion, path, expected, workspace):
    if not successful(completion) or path not in resolved_read_paths(start, workspace):
        return False
    content = result_text(completion)
    return isinstance(content, str) and normalized_content(expected) in normalized_content(content)


def task_write(start):
    name = start["data"]["toolName"]
    args = start["data"].get("arguments", {})
    if name in ("create", "edit", "apply_patch"):
        return True
    if name in ("bash", "shell"):
        command = args.get("command", "")
        return bool(re.search(r"(?<![2&])>(?!&)|write_text|json\.dump|(?:^|[;&]\s*)cp\s", command))
    return False


def write_destinations(start, workspace):
    """Conservative literal destination proof; dynamic/unsupported writes are uncredited."""
    name = start["data"]["toolName"]
    args = start["data"].get("arguments", {})
    if name in ("create", "edit") and isinstance(args.get("path"), str):
        path = Path(args["path"])
        return [str(path if path.is_absolute() else Path(workspace) / path)]
    if name not in ("bash", "shell"):
        return None
    command = args.get("command", "")
    try:
        # Preserve quoting so printf '>' cannot masquerade as a redirection.
        lexer = shlex.shlex(command, posix=False, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    cwd = Path(workspace)
    destinations = []
    at_start = True
    index = 0

    def literal(token):
        try:
            words = shlex.split(token)
        except ValueError:
            return None
        if len(words) != 1 or any(char in words[0] for char in "$`*?~"):
            return None
        return words[0]

    def path_for(token):
        word = literal(token)
        if word is None:
            return None
        path = Path(word)
        return str(path if path.is_absolute() else cwd / path)

    while index < len(tokens):
        token = tokens[index]
        if token in (";", "||", "|", "&"):
            return None
        if token == "&&":
            at_start = True
        elif token in (">", ">>"):
            if index + 1 >= len(tokens):
                return None
            destination = path_for(tokens[index + 1])
            if destination is None:
                return None
            destinations.append(destination)
            index += 1
        elif token == "<<":
            # The remainder is input payload, never a source of write operands.
            break
        elif at_start:
            if token not in ("cd", "cp", "cat", "printf", "echo"):
                return None
            if token == "cd":
                if index + 1 >= len(tokens):
                    return None
                destination = path_for(tokens[index + 1])
                if destination is None:
                    return None
                cwd = Path(destination)
                index += 1
            elif token == "cp":
                end = index + 1
                operands = []
                while end < len(tokens) and tokens[end] not in (";", "&&", "||", "|", ">", ">>", "<<"):
                    word = literal(tokens[end])
                    # Only the option-free two-operand form identifies a literal destination.
                    if word is None or word.startswith("-"):
                        return None
                    operands.append(tokens[end])
                    end += 1
                if len(operands) != 2:
                    return None
                destination = path_for(operands[-1])
                if destination is None:
                    return None
                destinations.append(destination)
            at_start = False
        index += 1
    return destinations or None


def recovery_event_errors(events, root, workspace=None):
    """Validate semantic reads/refresh before work, not a fixed tool choreography."""
    workspace = workspace or str(root)
    try:
        guidance = (root / "AGENTS.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [{"clear_event": None, "errors": ["durable guidance source unavailable or undecodable"]}]
    if not guidance.strip():
        return [{"clear_event": None, "errors": ["durable guidance source is empty"]}]
    clears = [i for i, event in enumerate(events)
              if event["type"] == "session.context_cleared" and root_event(event)]
    results = []
    for index, position in enumerate(clears):
        end = clears[index + 1] if index + 1 < len(clears) else len(events)
        window = [event for event in events[position + 1:end] if root_event(event)]
        tools = [event for event in window if event["type"] == "tool.execution_start"
                 and root_event(event)]
        completions = {event["data"]["toolCallId"]: event for event in window
                       if event["type"] == "tool.execution_complete"}
        errors = []
        checkpoint = None
        checkpoint_time = None
        guidance_reads = []
        skill_refreshes = []
        measurements = []
        work = []
        for event in tools:
            data = event["data"]
            name, args = data["toolName"], data.get("arguments", {})
            completion = completions.get(data["toolCallId"])
            if name == "session_artifacts" and args.get("operation") == "read" and args.get("path") == "checkpoint.md":
                if successful(completion) and checkpoint is None:
                    artifact, error = object_result(completion)
                    try:
                        checkpoint = json.loads(artifact["content"]) if not error else None
                    except (KeyError, TypeError, json.JSONDecodeError):
                        checkpoint = None
                    if not isinstance(checkpoint, dict):
                        errors.append("bound checkpoint read did not return valid content")
                        checkpoint = None
                    else:
                        checkpoint_time = completion["timestamp"]
            if content_read(event, completion, str(Path(workspace) / "AGENTS.md"), guidance, workspace):
                guidance_reads.append(completion)
            elif name == "skill" and args.get("skill") == "context-handoff":
                delivered = [entry for entry in window if entry["type"] == "skill.context_delivered_ref"
                             and entry["data"].get("source") == "skill-context-handoff"
                             and "/plugins/context-handoff/skills/context-handoff" in entry["data"].get("prefix", "")
                             and entry["timestamp"] >= event["timestamp"]
                             and successful(completion)
                             and entry["data"].get("interactionId") == completion["data"].get("interactionId")]
                if successful(completion) and delivered:
                    skill_refreshes.append({
                        "id": completion.get("id"),
                        "delivered_ref": delivered[0].get("id"),
                        "timestamp": max(completion["timestamp"], delivered[0]["timestamp"]),
                    })
            elif name == "get_context_remaining" and successful(completion):
                measurement, error = measurement_result(completion)
                if error:
                    errors.append(f"recovered-window measurement: {error}")
                elif measurement["context_window"]["number"] == index + 2:
                    measurements.append(completion)
                else:
                    errors.append("native measurement does not identify the recovered window")
            paths = resolved_read_paths(event, workspace)
            if any("corpus" in Path(path).parts for path in paths) or task_write(event):
                work.append(event)
        if not checkpoint:
            errors.append("bound checkpoint content was not successfully read")
        first_work = work[0] if work else None
        if not first_work:
            errors.append("recorded task action was not executed")
        for label, evidence in (("guidance content refresh", guidance_reads), ("owning targeted skill refresh", skill_refreshes)):
            if not evidence:
                errors.append(f"missing successful {label}")
            elif first_work and not any(item["timestamp"] < first_work["timestamp"] for item in evidence):
                errors.append(f"{label} did not complete before substantive task work")
        if not measurements:
            errors.append("no successful native measurement in the recovered window")
        if first_work and checkpoint_time and checkpoint_time >= first_work["timestamp"]:
            errors.append("task work preceded successful bound checkpoint read")
        if checkpoint and first_work:
            action = checkpoint.get("next_action", {})
            completion = completions.get(first_work["data"]["toolCallId"])
            if action.get("operation") == "read":
                try:
                    source = root / Path(action["path"]).relative_to(Path(workspace))
                    lo, hi = action["view_range"]
                    expected = "\n".join(source.read_text().splitlines()[lo - 1:hi])
                    if not content_read(first_work, completion, action["path"], expected, workspace):
                        errors.append("recorded task read content/path/range is not evidenced")
                except (OSError, KeyError, ValueError):
                    errors.append("recorded task read has invalid source binding")
            elif action.get("operation") == "write_witness":
                destinations = write_destinations(first_work, workspace)
                if (not successful(completion)
                        or destinations != [action.get("path")]):
                    errors.append("first substantive action does not prove a successful write to the recorded witness destination")
            else:
                errors.append("unsupported or stale recorded task action")
        results.append({
            "clear_event": events[position].get("id"),
            "guidance_content_reads": [item.get("id") for item in guidance_reads],
            "owning_skill_refreshes": [item.get("id") for item in skill_refreshes],
            "native_window_measurements": [item.get("id") for item in measurements],
            "first_substantive_work": first_work.get("id") if first_work else None,
            "errors": errors,
        })
    if len(clears) != 2:
        results.append({"clear_event": None, "errors": ["exactly two root clears required"]})
    return results


def pressure_events(events):
    return [event for event in events
            if event["type"] in ("session.info", "session.warning", "system.message", "user.message")
            and "[Context Handoff]" in str(event.get("data", {}))
            and root_event(event)]


def cursor_errors(cursor, root):
    if not isinstance(cursor, dict):
        return ["audit_cursor must be an object"]
    required = {"path", "last_line", "last_record", "last_severity", "next_path", "next_line"}
    if set(cursor) != required:
        return ["audit_cursor keys must be " + ", ".join(sorted(required))]
    try:
        path = (root / cursor["path"]).resolve()
        path.relative_to((root / "corpus").resolve())
        lines = path.read_text().splitlines()
        line = cursor["last_line"]
        if type(line) is not int or not 1 <= line <= len(lines):
            return ["last_line must be a real 1-based corpus line"]
        record = int(re.search(r"record=(\d+)", lines[line - 1]).group(1))
        severity = int(re.search(r"severity=(\d+)", lines[line - 1]).group(1))
        errors = []
        if cursor["last_record"] != record or cursor["last_severity"] != severity:
            errors.append("last_record/last_severity do not match last_line")
        if line < len(lines):
            expected_path, expected_line = cursor["path"], line + 1
        else:
            shard = int(re.search(r"shard-(\d+)", path.name).group(1)) + 1
            expected_path, expected_line = f"corpus/shard-{shard:03d}.txt", 1
        if (cursor["next_path"], cursor["next_line"]) != (expected_path, expected_line):
            errors.append(f"next cursor must be {expected_path}:{expected_line}")
        return errors
    except (OSError, ValueError, TypeError, AttributeError) as error:
        return [f"invalid audit cursor: {error}"]


def action_errors(action, cursor, transitions, workspace):
    if not isinstance(action, dict):
        return ["next_action must be one structured action object, not prose or a list"]
    if transitions == 1:
        if set(action) != {"operation", "path", "view_range"} or action.get("operation") != "read":
            return ["first successor action must be a single read, with only operation/path/view_range"]
        if action["path"] != str(Path(workspace) / cursor["next_path"]):
            return ["successor read path does not match the cursor"]
        bounds = action["view_range"]
        if (not isinstance(bounds, list) or len(bounds) != 2
                or any(type(n) is not int for n in bounds)
                or bounds[0] != cursor["next_line"] or not bounds[0] <= bounds[1] <= 80):
            return ["successor read range must begin at the exact next line and end by line 80"]
    elif transitions == 2:
        if action != {"operation": "write_witness", "path": str(Path(workspace) / "witness.json")}:
            return ["second successor action must only write witness.json"]
    else:
        return ["transitions_completed must be 1 or 2"]
    return []


def common_errors(document, root, task, workspace=None):
    errors = []
    for key in ("original_objective", "parent_gate", "current_slice",
                "unresolved_user_requests", "decisions", "live_references"):
        if document.get(key) != task[key]:
            errors.append(f"{key} must retain the authoritative task value")
    if document.get("workspace") != (workspace or str(root)):
        errors.append("workspace must be the actual synthetic cwd")
    if not isinstance(document.get("session_id"), str) or not document["session_id"]:
        errors.append("session_id is required")
    cursor = document.get("audit_cursor")
    errors.extend(cursor_errors(cursor, root))
    measurements = document.get("trigger_measurements")
    count = document.get("transition_number", document.get("transitions_completed"))
    if not isinstance(measurements, list) or len(measurements) != count:
        errors.append("trigger_measurements must retain one actual native result per completed transition")
    else:
        for index, measurement in enumerate(measurements):
            try:
                if measurement["context_info"]["promptTokenLimit"] != 200000:
                    errors.append("trigger measurement prompt limit changed")
                if not 0.30 <= measurement["fraction_remaining"] <= 0.40:
                    errors.append("trigger utilization must be between 60% and 70%")
                if measurement["context_window"]["number"] != index + 1:
                    errors.append("trigger measurements must cover successive windows in order")
            except (KeyError, TypeError):
                errors.append("trigger measurement must be the full actual native context result")
    return errors


def checkpoint_errors(document, root, task, workspace=None):
    errors = common_errors(document, root, task, workspace)
    cursor = document.get("audit_cursor")
    if document.get("transition_number") == 2 or (
            isinstance(cursor, dict) and {"next_path", "next_line"} <= cursor.keys()):
        errors.extend(action_errors(document.get("next_action"), cursor,
                                    document.get("transition_number"), workspace or str(root)))
    if "new_context" in json.dumps(document.get("next_action")):
        errors.append("terminal rollover is not a successor action")
    if document.get("recovery_prerequisites") != RECOVERY_PREREQUISITES:
        errors.append("semantic recovery_prerequisites must remain separate from next_action")
    return errors


def witness_errors(document, root, task, workspace=None):
    errors = common_errors(document, root, task, workspace)
    if document.get("transitions_completed") != 2:
        errors.append("witness requires exactly two completed transitions")
    if document.get("declaration") != "NOT-JUDGED; parent remains Active":
        errors.append("witness must not declare Tier-E or parent completion")
    if not isinstance(document.get("unexercised"), list) or not document["unexercised"]:
        errors.append("unexercised gates are required")
    if "next_action_fidelity" in document or "checkpoint_fidelity" in document:
        errors.append("do not substitute a fidelity assertion for checkpoint evidence")
    return errors


if __name__ == "__main__":
    kind, root_arg, *extra = sys.argv[1:]
    if kind == "recovery-events":
        if len(extra) != 1:
            raise SystemExit("recovery-events requires events.jsonl and the synthetic workspace directory")
        observations = recovery_event_errors([json.loads(line) for line in Path(root_arg).read_text().splitlines()],
                                             Path(extra[0]).resolve())
        print(json.dumps({"valid": all(not row["errors"] for row in observations), "observations": observations}))
        sys.exit(any(row["errors"] for row in observations))
    root = Path(root_arg).resolve()
    task = json.loads((root / "task.json").read_text())
    document = json.load(sys.stdin)
    validate = {"checkpoint": checkpoint_errors, "witness": witness_errors}[kind]
    errors = validate(document, root, task)
    print(json.dumps({"valid": not errors, "errors": errors}))
    sys.exit(bool(errors))
