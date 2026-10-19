"""Synthetic task validation, not a native artifact writer or Tier-E judge."""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
from fractions import Fraction

from checkpoint_binding import readback_errors, recovered_checkpoint
from event_parser import call_relationship_errors, event_order, event_stream
import snapshot_io

BASELINE_DIR = Path("/opt/copilot-cleanroom/native-cli-baseline")
RECOVERY_PREREQUISITES = [
    "read_bound_checkpoint",
    "read_AGENTS.md",
    "refresh_context-handoff_skill",
    "get_context_remaining",
]


def capture_baseline(workspace, destination, session_id):
    """Setup-only capture; the live CLI invokes this as root before any driven turn."""
    workspace, destination = workspace.resolve(), destination.resolve()
    if destination == workspace or workspace in destination.parents:
        raise ValueError("seed baseline must be outside the driven workspace")
    if destination.exists():
        raise ValueError("seed baseline already exists; refusing recapture")
    names = ["AGENTS.md", "task.json", "audit-contract.md", "ledger.json", ".context-handoff/config.yaml"]
    names += [path.relative_to(workspace).as_posix() for path in sorted((workspace / "corpus").glob("*.txt"))]
    if not names[5:] or not isinstance(session_id, str) or not session_id:
        raise ValueError("complete corpus and session identity required before seed capture")
    destination.mkdir(parents=True)
    hashes = {}
    for name in names:
        source = workspace / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"seed input is absent or not a regular file: {name}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        contents = source.read_bytes()
        target.write_bytes(contents)
        hashes[name] = hashlib.sha256(contents).hexdigest()
    manifest = {"schema": 1, "workspace": str(workspace), "session_id": session_id, "files_sha256": hashes}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for path in destination.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    destination.chmod(0o555)
    return manifest


def seed_baseline(baseline=None):
    directory = Path(baseline) if baseline is not None else BASELINE_DIR
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("schema") != 1
                or not isinstance(manifest.get("workspace"), str) or not manifest["workspace"]
                or not isinstance(manifest.get("session_id"), str) or not manifest["session_id"]
                or not isinstance(manifest.get("files_sha256"), dict)):
            return None, ["seed baseline manifest is invalid"]
        hashes = manifest["files_sha256"]
        required = {"AGENTS.md", "task.json", "audit-contract.md", "ledger.json", ".context-handoff/config.yaml"}
        if not required <= hashes.keys() or not any(name.startswith("corpus/") for name in hashes):
            return None, ["seed baseline is incomplete"]
        for name, digest in hashes.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or not isinstance(digest, str):
                return None, ["seed baseline contains an invalid file binding"]
            path = directory / relative
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return None, [f"seed baseline integrity failed: {name}"]
        task = json.loads((directory / "task.json").read_text(encoding="utf-8"))
        if not isinstance(task, dict) or not all(key in task for key in (
                "original_objective", "parent_gate", "current_slice", "unresolved_user_requests", "decisions", "live_references")):
            return None, ["seed task baseline is not an object"]
        return {"directory": directory, "manifest": manifest, "task": task}, []
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None, ["seed baseline missing or unreadable; mutable workspace is not a fallback"]


def workspace_seed_errors(workspace, seed):
    errors = []
    for name, digest in seed["manifest"]["files_sha256"].items():
        path = workspace / name
        try:
            if hashlib.sha256(snapshot_io.read_bytes(path)).hexdigest() != digest:
                errors.append(f"driven seed input changed: {name}")
        except OSError:
            errors.append(f"driven seed input unavailable: {name}")
    return errors


def seal_disposable_venue(workspace, baseline, sudoers=Path("/etc/sudoers.d"),
                          sudo_binary=Path("/usr/bin/sudo"), container_flag=Path("/.dockerenv"),
                          policy_check=None):
    """Root setup only: remove the clean-room grant and executable escalation path."""
    seed, errors = seed_baseline(baseline)
    if (errors or not container_flag.is_file() or seed["manifest"]["workspace"] != str(workspace.resolve())
            or workspace_seed_errors(workspace, seed)):
        raise ValueError("refusing to seal an unverified disposable venue")
    grant = sudoers / "operator"
    try:
        expected_grant = grant.read_text().strip()
    except (OSError, UnicodeError) as error:
        raise ValueError("clean-room sudo grant missing or unreadable; refusing seal before mutation") from error
    if expected_grant != "operator ALL=(ALL) NOPASSWD:ALL":
        raise ValueError("unexpected clean-room sudo grant; refusing host/shared policy changes")
    deny = sudoers / "zz-native-cli-operator-deny"
    if deny.exists():
        raise ValueError("venue already sealed; refusing policy replacement")
    deny.write_text("operator ALL=(ALL:ALL) !ALL\n")
    deny.chmod(0o440)
    grant.unlink()
    sudo_binary.chmod(stat.S_IMODE(sudo_binary.stat().st_mode) & ~(stat.S_ISUID | stat.S_ISGID))
    if sudo_binary.stat().st_mode & (stat.S_ISUID | stat.S_ISGID):
        raise RuntimeError("sudo elevation bits were not removed")
    if policy_check is None:
        subprocess.run(["visudo", "-c"], check=True)
    else:
        policy_check()
    marker = {"schema": 1, "session_id": seed["manifest"]["session_id"],
              "operator_sudo_denied": True, "sudo_elevation_bits_removed": True}
    path = baseline / "venue-seal.json"
    path.write_text(json.dumps(marker, indent=2) + "\n")
    path.chmod(0o444)
    return marker


def result_text(completion):
    result = completion.get("data", {}).get("result")
    return result.get("content") if isinstance(result, dict) else None


def execution_status(completion):
    invalid = {"success": False, "shell_exit_code": None}
    if not isinstance(completion, dict) or not isinstance(completion.get("data"), dict):
        return dict(invalid, error="invalid execution data")
    data = completion["data"]
    for key in ("shellExecution", "toolTelemetry"):
        if key in data and not isinstance(data[key], dict):
            return dict(invalid, error=f"invalid optional {key} metadata")
    shell = data.get("shellExecution", {})
    telemetry = data.get("toolTelemetry", {})
    if "metrics" in telemetry and not isinstance(telemetry["metrics"], dict):
        return dict(invalid, error="invalid optional toolTelemetry.metrics metadata")
    metrics = telemetry.get("metrics", {})
    for container, key in ((shell, "exitCode"), (metrics, "exit_code")):
        if key in container and type(container[key]) is not int:
            return dict(invalid, error=f"invalid optional {key} value")
    exit_code, telemetry_code = shell.get("exitCode"), metrics.get("exit_code")
    good = data.get("success") is True and exit_code in (None, 0) and telemetry_code in (None, 0)
    return {"success": good, "shell_exit_code": exit_code,
            "error": None if good else "tool execution unsuccessful"}


def successful(completion):
    return execution_status(completion)["success"]


def root_event(event):
    data = event.get("data", {})
    return event.get("agentId") is None and isinstance(data, dict) and data.get("agentId") is None


def object_result(completion):
    status = execution_status(completion)
    if not status["success"]:
        return None, status["error"]
    try:
        payload = json.loads(result_text(completion))
    except (json.JSONDecodeError, TypeError):
        return None, "tool result is not valid JSON"
    if not isinstance(payload, dict):
        return None, "tool result is not an object"
    return payload, None


def finite_number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


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
            or not finite_number(limit) or limit <= 0
            or not finite_number(left) or not 0 <= left <= limit
            or not finite_number(fraction) or not 0 <= fraction <= 1):
        return None, "native measurement lacks valid captured window/token fields"
    # CLI 1.0.92 captured ratios (e.g. 173839/200000 -> .869195) serialize
    # the exact binary result. No additional decimal-rounding tolerance exists
    # in the observed format. Fraction keeps huge integer inputs overflow-safe.
    ratio = float(Fraction(left) / Fraction(limit))
    if fraction != ratio:
        return None, "native fraction_remaining differs from tokens_left/promptTokenLimit"
    return payload, None


def checkpoint_readback_errors(write_row, read_row, clear, owner, *, order, after_clear=False):
    return readback_errors(write_row, read_row, clear, owner, root_event=root_event,
                           object_result=object_result, order=order, after_clear=after_clear)


def normalized_content(text):
    return re.sub(r"(?m)^L\d+:", "", text.replace("\r\n", "\n")).strip()


def resolved_read_paths(start, workspace):
    """Track potential task operands; content_read separately proves producer/output."""
    name = start["data"]["toolName"]
    args = start["data"].get("arguments", {})
    if name == "view" and isinstance(args.get("path"), str):
        path = Path(args["path"])
        return [str(path if path.is_absolute() else Path(workspace) / path)], []
    if name not in ("bash", "shell"):
        return [], []
    try:
        lexer = shlex.shlex(args.get("command", ""), posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return [], []
    cwd = Path(workspace)
    paths, errors = [], []
    index = 0
    command_start = True
    while index < len(tokens):
        token = tokens[index]
        reader = Path(token).name
        if token in (";", "&&", "||", "|"):
            command_start = True
            index += 1
            continue
        if command_start and token == "cd" and index + 1 < len(tokens):
            operand = Path(tokens[index + 1])
            cwd = operand if operand.is_absolute() else cwd / operand
        if command_start and reader in ("cat", "head", "tail", "sed", "grep", "rg", "wc", "stat", "ls"):
            end = index + 1
            while end < len(tokens) and tokens[end] not in (";", "&&", "||", "|"):
                end += 1
            operands, _, recursive = reader_inputs(reader, tokens[index + 1:end], include_recursion=True)
            if not operands and recursive:
                operands = [str(cwd)]
            elif not operands and reader == "ls":
                paths.append(str(cwd))
            for operand in operands:
                path = Path(operand)
                resolved = path if path.is_absolute() else cwd / path
                paths.append(str(resolved))
                if recursive:
                    try:
                        corpus = (Path(workspace) / "corpus").resolve()
                        corpus.relative_to(resolved.resolve())
                        paths.append(str(corpus))
                    except ValueError:
                        pass  # A recursive reader outside the fixture is not corpus work.
                    except (OSError, RuntimeError):
                        errors.append("recursive reader path unavailable or unresolvable")
        command_start = False
        index += 1
    return paths, errors


def potential_read_operands(reader, words):
    return reader_inputs(reader, words)[0]


def reader_inputs(reader, words, *, include_recursion=False):
    """Return potential input files and preprocessing risk, never content-read credit."""
    if reader not in ("grep", "rg"):
        files = [word for word in words if not word.startswith("-")
                 and (reader not in ("head", "tail", "sed") or "/" in word or word.endswith((".md", ".txt")))]
        return (files, False, False) if include_recursion else (files, False)
    files, index, has_pattern, options, executes, recursive = [], 0, False, True, False, reader == "rg"
    long_values = set(("after-context before-context context max-count binary-files directories devices "
                       "exclude exclude-dir include label").split())
    if reader == "rg":
        long_values.update(("glob iglob type type-not type-add type-clear color encoding engine threads "
                            "max-columns max-depth max-filesize sort sortr path-separator replace pre pre-glob").split())
    short_values = set("ABCm" + ("gtTMj" if reader == "rg" else "dD"))
    while index < len(words):
        word = words[index]
        if options and word == "--":
            options = False
            index += 1
            continue
        kind, attached = None, None
        if options and word.startswith("--"):
            name, separator, value = word[2:].partition("=")
            if name == "no-pre" and reader == "rg":
                executes = False
            elif name == ("ignore-file" if reader == "rg" else "exclude-from"):
                kind = "filter-file"
            elif name in ("regexp", "file"):
                kind = "pattern" if name == "regexp" else "file"
            elif name in long_values:
                kind = "value"
            elif name == "files" and reader == "rg":
                has_pattern = True
            elif name in ("recursive", "dereference-recursive") and reader == "grep":
                recursive = True
            if separator:
                attached = value
        elif options and word.startswith("-") and word != "-":
            for position, flag in enumerate(word[1:]):
                if flag in "ef" or flag in short_values:
                    kind = "pattern" if flag == "e" else "file" if flag == "f" else "value"
                    attached = word[position + 2:] or None
                    break
                if reader == "grep" and flag in "rR":
                    recursive = True
        if kind is not None:
            if attached is None and index + 1 < len(words):
                index += 1
                attached = words[index]
            if kind in ("pattern", "file"):
                has_pattern = True
            if kind in ("file", "filter-file") and attached:
                files.append(attached)
            if reader == "rg" and word.partition("=")[0] == "--pre":
                executes = bool(attached)
        elif not options or not word.startswith("-") or word == "-":
            if not has_pattern:
                has_pattern = True
            else:
                files.append(word)
        index += 1
    return (files, executes, recursive) if include_recursion else (files, executes)


def valid_read_range(bounds, upper=None):
    return (isinstance(bounds, list) and len(bounds) == 2
            and all(type(value) is int for value in bounds)
            and 1 <= bounds[0] <= bounds[1]
            and (upper is None or bounds[1] <= upper))


def shell_read_specs(command, workspace):
    """Only literal && chains of actual readers can attribute pooled stdout."""
    if not isinstance(command, str) or any(character in command for character in "$`\n"):
        return None
    try:
        lexer = shlex.shlex(command, posix=False, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    if any(token in (";", "|", "||", "&", "<", "<<", ">", ">>", ">&", "&>") for token in tokens):
        return None
    segments = [[]]
    for token in tokens:
        if token == "&&":
            segments.append([])
        else:
            segments[-1].append(token)
    cwd = Path(workspace)
    specs = []
    for segment in segments:
        try:
            words = [shlex.split(token) for token in segment]
        except ValueError:
            return None
        if not words or any(len(word) != 1 for word in words):
            return None
        words = [word[0] for word in words]
        if any(any(character in word for character in "*?~") for word in words):
            return None
        reader = words[0]
        bounds = None
        if reader == "cd" and len(words) == 2:
            path = Path(words[1])
            cwd = path if path.is_absolute() else cwd / path
            continue
        if reader == "cat" and len(words) == 2 and words[1] and not words[1].startswith("-"):
            operand = words[1]
        elif reader == "sed" and len(words) == 4 and words[1] == "-n":
            match = re.fullmatch(r"([1-9]\d*)(?:,([1-9]\d*))?p", words[2])
            if match is None or not words[3] or words[3].startswith("-"):
                return None
            bounds = [int(match[1]), int(match[2] or match[1])]
            if not valid_read_range(bounds):
                return None
            operand = words[3]
        else:
            return None
        path = Path(operand)
        specs.append({"path": str(path if path.is_absolute() else cwd / path), "bounds": bounds})
    return specs or None


def content_read(start, completion, path, expected, workspace, view_range=None):
    if not successful(completion):
        return False
    paths, path_errors = resolved_read_paths(start, workspace)
    if path_errors or path not in paths:
        return False
    if start["data"]["toolName"] in ("bash", "shell"):
        specs = shell_read_specs(start["data"].get("arguments", {}).get("command"), workspace)
        if specs is None:
            return False
        if view_range is None:
            full_lines = len(expected.splitlines())
            attributed = any(spec["path"] == path and (spec["bounds"] is None or
                             (spec["bounds"][0] == 1 and spec["bounds"][1] >= full_lines))
                             for spec in specs)
        else:
            attributed = any(spec["path"] == path and spec["bounds"] == view_range for spec in specs)
        if not attributed:
            return False
    content = result_text(completion)
    if not isinstance(content, str) or not normalized_content(expected):
        return False
    if view_range is not None:
        if not valid_read_range(view_range):
            return False
        args = start["data"].get("arguments", {})
        if start["data"]["toolName"] == "view" and "view_range" in args:
            if not valid_read_range(args["view_range"]) or args["view_range"] != view_range:
                return False
        # Bounded task reads must not earn credit from a substring of a wider
        # result. Remove only the captured shell completion footer.
        bounded = re.sub(r"\n?<shellId: [^\n<>]+ completed with exit code 0>\s*$", "", content)
        return normalized_content(bounded) == normalized_content(expected)
    return normalized_content(expected) in normalized_content(content)


def task_write(start):
    name = start["data"]["toolName"]
    args = start["data"].get("arguments", {})
    if name in ("create", "edit", "apply_patch"):
        return True
    if name in ("bash", "shell"):
        command = args.get("command", "")
        if not isinstance(command, str):
            return True
        if re.search(r">|<\(|write_text|json\.dump|[$`]", command):
            return True
        try:
            lexer = shlex.shlex(command.replace("\n", ";"), posix=True, punctuation_chars=";&|")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            return True
        readonly = {"cd", "cat", "head", "tail", "pwd", "ls", "grep", "rg", "wc", "stat",
                    "echo", "printf", "true", "false", "test", "["}
        at_start = True
        for index, token in enumerate(tokens):
            if token in (";", "&&", "||", "|", "&"):
                at_start = True
                continue
            if not at_start:
                continue
            at_start = False
            executable = Path(token).name
            if executable in ("sed", "rg"):
                end = index + 1
                while end < len(tokens) and tokens[end] not in (";", "&&", "||", "|", "&"):
                    end += 1
                operands = tokens[index + 1:end]
                if executable == "rg":
                    if reader_inputs("rg", operands)[1]:
                        return True
                    continue
                # Only the fixture's simple bounded-print form is proven
                # read-only; other scripts can write or execute without -i.
                if (len(operands) != 3 or operands[0] != "-n"
                        or re.fullmatch(r"[1-9]\d*(?:,[1-9]\d*)?p", operands[1]) is None
                        or not operands[2] or operands[2].startswith("-")):
                    return True
            elif executable not in readonly:
                # Unknown commands/interpreters may mutate files. Retain them
                # as substantive, uncredited work instead of skipping them.
                return True
        return False
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
            if tokens[index + 1] == "&":
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


def recovery_event_errors(events, root, workspace=None, baseline=None):
    """Validate semantic reads/refresh before work, not a fixed tool choreography."""
    relationship_errors = call_relationship_errors(list(enumerate(events, 1)))
    if relationship_errors:
        return [{"clear_event": None, "errors": [error["error"] for error in relationship_errors]}]
    order = event_order(events)
    seed, baseline_errors = seed_baseline(baseline)
    if baseline_errors:
        return [{"clear_event": None, "errors": baseline_errors}]
    workspace = workspace or seed["manifest"]["workspace"]
    integrity_errors = workspace_seed_errors(root, seed)
    if integrity_errors:
        return [{"clear_event": None, "errors": integrity_errors}]
    try:
        guidance = (seed["directory"] / "AGENTS.md").read_text(encoding="utf-8")
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
        previous = clears[index - 1] + 1 if index else 0
        preclear = events[previous:position]
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
                if checkpoint is None:
                    checkpoint, binding_errors = recovered_checkpoint(
                        preclear, events[position], {"start": event, "complete": completion},
                        seed["manifest"]["session_id"], index, root_event=root_event,
                        object_result=object_result, check_readback=checkpoint_readback_errors, order=order)
                    if binding_errors:
                        errors.extend(binding_errors)
                    elif checkpoint is not None:
                        checkpoint_time = order(completion)
            if content_read(event, completion, str(Path(workspace) / "AGENTS.md"), guidance, workspace):
                guidance_reads.append(completion)
            elif name == "skill" and args.get("skill") == "context-handoff":
                delivered = [entry for entry in window if entry["type"] == "skill.context_delivered_ref"
                             and entry["data"].get("source") == "skill-context-handoff"
                             and "/plugins/context-handoff/skills/context-handoff" in entry["data"].get("prefix", "")
                             and order(entry) > order(event)
                             and successful(completion)
                             and entry["data"].get("interactionId") == completion["data"].get("interactionId")]
                if successful(completion) and delivered:
                    skill_refreshes.append({
                        "id": completion.get("id"),
                        "delivered_ref": delivered[0].get("id"),
                        "timestamp": max(completion["timestamp"], delivered[0]["timestamp"]),
                        "stream_position": max(order(completion), order(delivered[0])),
                    })
            elif name == "get_context_remaining" and successful(completion):
                measurement, error = measurement_result(completion)
                if error:
                    errors.append(f"recovered-window measurement: {error}")
                elif measurement["context_window"]["number"] == index + 2:
                    measurements.append(completion)
                else:
                    errors.append("native measurement does not identify the recovered window")
            paths, path_errors = resolved_read_paths(event, workspace)
            errors.extend(path_errors)
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
            elif first_work and not any(
                    (item["stream_position"] if label == "owning targeted skill refresh" else order(item))
                    < order(first_work) for item in evidence):
                errors.append(f"{label} did not complete before substantive task work")
        if not measurements:
            errors.append("no successful native measurement in the recovered window")
        if first_work and checkpoint_time is not None and checkpoint_time >= order(first_work):
            errors.append("task work preceded successful bound checkpoint read")
        if checkpoint and first_work:
            action = checkpoint.get("next_action")
            completion = completions.get(first_work["data"]["toolCallId"])
            if not isinstance(action, dict):
                errors.append("recorded task action is not an object")
            elif action.get("operation") == "read":
                path, bounds = action.get("path"), action.get("view_range")
                if (set(action) != {"operation", "path", "view_range"}
                        or not isinstance(path, str) or not path.strip() or not valid_read_range(bounds)):
                    errors.append("recorded task read has invalid source binding")
                else:
                    try:
                        source = seed["directory"] / Path(path).relative_to(Path(workspace))
                        lines = source.read_text().splitlines()
                        if not valid_read_range(bounds, len(lines)):
                            errors.append("recorded task read range is outside source bounds")
                        else:
                            lo, hi = bounds
                            expected = "\n".join(lines[lo - 1:hi])
                            if not content_read(first_work, completion, path, expected, workspace, bounds):
                                errors.append("recorded task read content/path/range is not evidenced")
                    except (OSError, ValueError):
                        errors.append("recorded task read has invalid source binding")
            elif action.get("operation") == "write_witness":
                destinations = write_destinations(first_work, workspace)
                if (set(action) != {"operation", "path"} or not isinstance(action.get("path"), str)
                        or not action["path"].strip() or not successful(completion)
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


def bound_seed_corpus(seed, name):
    if not isinstance(name, str) or not name.startswith("corpus/") or name not in seed["manifest"]["files_sha256"]:
        return None, "corpus path is not bound in the protected seed manifest"
    try:
        path = seed["directory"] / name
        path.resolve().relative_to((seed["directory"] / "corpus").resolve())
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError, ValueError):
        return None, "bound seed corpus is unreadable or invalid"
    if not lines:
        return None, "bound seed corpus is empty; no readable resume cursor"
    return lines, None


def cursor_errors(cursor, root, baseline=None):
    seed, errors = seed_baseline(baseline)
    if errors:
        return errors
    if not isinstance(cursor, dict):
        return ["audit_cursor must be an object"]
    required = {"path", "last_line", "last_record", "last_severity", "next_path", "next_line"}
    if set(cursor) != required:
        return ["audit_cursor keys must be " + ", ".join(sorted(required))]
    for key in ("last_record", "last_severity", "next_line"):
        if type(cursor[key]) is not int:
            return [f"{key} must be a genuine integer"]
    try:
        lines, error = bound_seed_corpus(seed, cursor["path"])
        if error:
            return [f"current cursor: {error}"]
        path = seed["directory"] / cursor["path"]
        line = cursor["last_line"]
        if type(line) is not int or not 1 <= line <= len(lines):
            return ["last_line must be a real 1-based corpus line"]
        record_match = re.search(r"record=(\d+)", lines[line - 1])
        severity_match = re.search(r"severity=(\d+)", lines[line - 1])
        if record_match is None or severity_match is None:
            return ["last_line does not contain record/severity fields"]
        record, severity = int(record_match[1]), int(severity_match[1])
        errors = []
        if cursor["last_record"] != record or cursor["last_severity"] != severity:
            errors.append("last_record/last_severity do not match last_line")
        if line < len(lines):
            expected_path, expected_line = cursor["path"], line + 1
        else:
            shard_match = re.search(r"shard-(\d+)", path.name)
            if shard_match is None:
                return ["last path must identify a numbered corpus shard"]
            shard = int(shard_match[1]) + 1
            expected_path, expected_line = f"corpus/shard-{shard:03d}.txt", 1
        if (cursor["next_path"], cursor["next_line"]) != (expected_path, expected_line):
            errors.append(f"next cursor must be {expected_path}:{expected_line}")
        for label, name, next_line in (("computed next", expected_path, expected_line),
                                       ("declared next", cursor["next_path"], cursor["next_line"])):
            next_lines, error = bound_seed_corpus(seed, name)
            if error:
                errors.append(f"{label} cursor: {error}")
            elif not 1 <= next_line <= len(next_lines):
                errors.append(f"{label} cursor line is not readable in the protected seed corpus")
        return errors
    except (OSError, ValueError, TypeError):
        return ["invalid audit cursor: unreadable or invalid corpus binding"]


def action_errors(action, cursor, transitions, workspace, baseline=None):
    if type(transitions) is not int or transitions not in (1, 2):
        return ["transition count must be a genuine integer 1 or 2"]
    if not isinstance(action, dict):
        return ["next_action must be one structured action object, not prose or a list"]
    if not isinstance(action.get("path"), str) or not action["path"].strip():
        return ["next_action path must be a nonempty string"]
    if transitions == 1:
        if set(action) != {"operation", "path", "view_range"} or action.get("operation") != "read":
            return ["first successor action must be a single read, with only operation/path/view_range"]
        if (not isinstance(cursor, dict) or not isinstance(cursor.get("next_path"), str)
                or type(cursor.get("next_line")) is not int):
            return ["successor read cursor binding is invalid"]
        if action["path"] != str(Path(workspace) / cursor["next_path"]):
            return ["successor read path does not match the cursor"]
        bounds = action["view_range"]
        if not valid_read_range(bounds, 80) or bounds[0] != cursor["next_line"]:
            return ["successor read range must begin at the exact next line and end by line 80"]
        seed, errors = seed_baseline(baseline)
        if errors:
            return errors
        lines, error = bound_seed_corpus(seed, cursor["next_path"])
        if error:
            return [f"successor action: {error}"]
        if not valid_read_range(bounds, len(lines)):
            return ["successor action range is not readable in the protected seed corpus"]
    elif transitions == 2:
        if action != {"operation": "write_witness", "path": str(Path(workspace) / "witness.json")}:
            return ["second successor action must only write witness.json"]
    else:
        return ["transitions_completed must be 1 or 2"]
    return []


def common_errors(document, root, *, workspace=None, baseline=None, kind="checkpoint"):
    seed, errors = seed_baseline(baseline)
    if errors:
        return errors
    task = seed["task"]
    errors = workspace_seed_errors(root, seed)
    for key in ("original_objective", "parent_gate", "current_slice",
                "unresolved_user_requests", "decisions", "live_references"):
        if document.get(key) != task[key]:
            errors.append(f"{key} must retain the authoritative task value")
    if document.get("workspace") != (workspace or seed["manifest"]["workspace"]):
        errors.append("workspace must be the actual synthetic cwd")
    if document.get("session_id") != seed["manifest"]["session_id"]:
        errors.append("session_id must match the trusted seed identity")
    cursor = document.get("audit_cursor")
    errors.extend(cursor_errors(cursor, root, baseline))
    measurements = document.get("trigger_measurements")
    count_key = "transition_number" if kind == "checkpoint" else "transitions_completed"
    wrong_key = "transitions_completed" if kind == "checkpoint" else "transition_number"
    count = document.get(count_key)
    if wrong_key in document:
        errors.append(f"{wrong_key} is not a field for this document kind")
    if ("transition_number" in document and "transitions_completed" in document
            and document["transition_number"] != document["transitions_completed"]):
        errors.append("transition counters disagree")
    for key in ("transition_number", "transitions_completed"):
        if key in document and (type(document[key]) is not int or document[key] not in (1, 2)):
            errors.append(f"{key} must be a genuine integer 1 or 2")
    if type(count) is not int or count not in (1, 2):
        errors.append("transition count must be a genuine integer 1 or 2")
    if not isinstance(measurements, list) or len(measurements) != count:
        errors.append("trigger_measurements must retain one actual native result per completed transition")
    else:
        for index, measurement in enumerate(measurements):
            captured, error = measurement_result({"data": {"success": True,
                                                          "result": {"content": json.dumps(measurement)}}})
            if error:
                errors.append(f"trigger measurement: {error}")
                continue
            try:
                if captured["context_info"]["promptTokenLimit"] != 200000:
                    errors.append("trigger measurement prompt limit changed")
                if not 0.30 <= measurement["fraction_remaining"] <= 0.40:
                    errors.append("trigger utilization must be between 60% and 70%")
                if (type(measurement["context_window"]["number"]) is not int
                        or measurement["context_window"]["number"] != index + 1):
                    errors.append("trigger measurements must cover successive windows in order")
            except (KeyError, TypeError):
                errors.append("trigger measurement must be the full actual native context result")
    return errors


def checkpoint_errors(document, root, *, workspace=None, baseline=None):
    errors = common_errors(document, root, workspace=workspace, baseline=baseline, kind="checkpoint")
    cursor = document.get("audit_cursor")
    if document.get("transition_number") == 2 or (
            isinstance(cursor, dict) and {"next_path", "next_line"} <= cursor.keys()):
        errors.extend(action_errors(document.get("next_action"), cursor,
                                    document.get("transition_number"), workspace or str(root), baseline))
    if "new_context" in json.dumps(document.get("next_action")):
        errors.append("terminal rollover is not a successor action")
    if document.get("recovery_prerequisites") != RECOVERY_PREREQUISITES:
        errors.append("semantic recovery_prerequisites must remain separate from next_action")
    return errors


def witness_errors(document, root, *, workspace=None, baseline=None):
    errors = common_errors(document, root, workspace=workspace, baseline=baseline, kind="witness")
    if type(document.get("transitions_completed")) is not int or document["transitions_completed"] != 2:
        errors.append("witness requires exactly two completed transitions")
    if document.get("declaration") != "NOT-JUDGED; parent remains Active":
        errors.append("witness must not declare Tier-E or parent completion")
    if not isinstance(document.get("unexercised"), list) or not document["unexercised"]:
        errors.append("unexercised gates are required")
    if "next_action_fidelity" in document or "checkpoint_fidelity" in document:
        errors.append("do not substitute a fidelity assertion for checkpoint evidence")
    return errors


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("usage: validation.py <command> <workspace-or-events> <command-arguments>")
    kind, root_arg, *extra = sys.argv[1:]
    if kind not in ("capture-baseline", "verify-baseline", "seal-venue", "recovery-events", "checkpoint", "witness"):
        raise SystemExit("usage: validation.py <capture-baseline|verify-baseline|seal-venue|recovery-events|checkpoint|witness> ...")
    if kind == "capture-baseline":
        if (len(extra) != 2 or (os.name == "posix" and os.geteuid() != 0)
                or Path(extra[0]).resolve() != BASELINE_DIR.resolve()):
            raise SystemExit("capture-baseline requires root setup, destination and session id")
        print(json.dumps(capture_baseline(Path(root_arg), Path(extra[0]), extra[1])))
        sys.exit(0)
    if kind == "verify-baseline":
        if len(extra) != 1:
            raise SystemExit("verify-baseline requires workspace and trusted seed baseline")
        seed, errors = seed_baseline(Path(extra[0]))
        if seed is not None:
            errors.extend(workspace_seed_errors(Path(root_arg), seed))
        print(json.dumps({"valid": not errors, "errors": errors}))
        sys.exit(bool(errors))
    if kind == "seal-venue":
        if (len(extra) != 1 or os.name != "posix" or os.geteuid() != 0
                or Path(extra[0]).resolve() != BASELINE_DIR.resolve()):
            raise SystemExit("seal-venue is root-only disposable clean-room setup")
        print(json.dumps(seal_disposable_venue(Path(root_arg).resolve(), Path(extra[0]).resolve())))
        sys.exit(0)
    if kind == "recovery-events":
        if len(extra) != 2:
            raise SystemExit("recovery-events requires events.jsonl, workspace and trusted seed baseline")
        events, stream_errors = event_stream(Path(root_arg))
        observations = [{"clear_event": None, "errors": stream_errors}] if stream_errors else \
            recovery_event_errors(events, Path(extra[0]).resolve(), baseline=Path(extra[1]))
        print(json.dumps({"valid": all(not row["errors"] for row in observations), "observations": observations}))
        sys.exit(any(row["errors"] for row in observations))
    root = Path(root_arg).resolve()
    if len(extra) != 1:
        raise SystemExit("checkpoint/witness validation requires a trusted seed baseline")
    try:
        document = json.load(sys.stdin)
    except (OSError, UnicodeError, json.JSONDecodeError):
        print(json.dumps({"valid": False, "errors": ["checkpoint/witness content is not valid JSON"]}))
        sys.exit(1)
    if not isinstance(document, dict):
        print(json.dumps({"valid": False, "errors": ["checkpoint/witness content must be an object"]}))
        sys.exit(1)
    validate = {"checkpoint": checkpoint_errors, "witness": witness_errors}[kind]
    errors = validate(document, root, baseline=Path(extra[0]))
    print(json.dumps({"valid": not errors, "errors": errors}))
    sys.exit(bool(errors))
