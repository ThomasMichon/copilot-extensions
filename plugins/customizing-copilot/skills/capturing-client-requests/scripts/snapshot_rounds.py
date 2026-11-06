"""Bounded fake-provider conversation for the owned client's session guidance."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

MAX_ROUNDS = 3
MAX_FILES = 64
MAX_FILE_BYTES = 256 * 1024
MAX_GUIDANCE_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_INVENTORY_ENTRIES = 512
TERMINAL_REPLY = "LOCAL_REQUEST_CAPTURE_COMPLETE"


def text_content(message: dict) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
        isinstance(part, dict) and part.get("type") == "text"
        and isinstance(part.get("text"), str) for part in content
    ):
        return "\n".join(part["text"] for part in content)
    return None


def accepts(schema: dict, kind: str) -> bool:
    return isinstance(schema, dict) and (
        schema.get("type") == kind
        or any(accepts(part, kind) for part in schema.get("anyOf", []))
        or any(accepts(part, kind) for part in schema.get("oneOf", []))
    )


def advertised_tool(payload: dict, name: str, arguments: dict) -> str | None:
    tools = payload.get("tools", [])
    if not isinstance(tools, list):
        return None
    matches = [
        tool["function"] for tool in tools
        if isinstance(tool, dict) and tool.get("type") == "function"
        and isinstance(tool.get("function"), dict)
        and tool["function"].get("name") == name
    ]
    if len(matches) != 1:
        return None
    schema = matches[0].get("parameters", {})
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        return None
    if any(key not in arguments for key in required):
        return None
    for key, value in arguments.items():
        kind = "boolean" if isinstance(value, bool) else "string"
        if not accepts(properties.get(key, {}), kind):
            return None
    return name


def tool_call(identifier: str, name: str, arguments: dict) -> dict:
    return {
        "id": identifier, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class GuidanceSnapshot:
    def __init__(self, instructions: Path):
        self.instructions = instructions.absolute()
        self.rounds: list[dict] = []
        self.pending: dict[str, str] = {}
        self.selected: dict[str, dict] = {}
        self.state = "initial"
        self.reason: str | None = None

    def inventory(self) -> dict:
        root = self.instructions
        files = []
        if not root.exists():
            return {"status": "absent", "files": files}
        if root.resolve() != root or not root.is_dir():
            return {"status": "unsafe_root", "files": files}
        examined = 0
        directories = [root]
        try:
            while directories:
                with os.scandir(directories.pop()) as entries:
                    for entry in entries:
                        examined += 1
                        if examined > MAX_INVENTORY_ENTRIES:
                            return {"status": "entry_limit", "files": files}
                        path = Path(entry.path)
                        if entry.is_dir(follow_symlinks=False):
                            if path.resolve() == path:
                                directories.append(path)
                            continue
                        if path.suffix != ".md":
                            continue
                        if path.resolve() != path or not path.is_file():
                            return {"status": "unsafe_file", "files": files}
                        stat = path.stat()
                        files.append({
                            "path": str(path), "bytes": stat.st_size,
                            "mtime_ns": stat.st_mtime_ns,
                        })
                        if len(files) > MAX_FILES:
                            return {"status": "file_limit", "files": files}
        except OSError:
            return {"status": "unavailable", "files": files}
        return {"status": "present", "files": sorted(files, key=lambda item: item["path"])}

    def finish(self, reason: str | None = None) -> dict:
        self.state = "incomplete" if reason else "complete"
        self.reason = reason
        return {"role": "assistant", "content": TERMINAL_REPLY}

    @property
    def finished(self) -> bool:
        return self.state in {"complete", "incomplete"}

    def report(self) -> dict:
        return {
            "status": self.state, "reason": self.reason,
            "session_start_completion": "not_observable",
            "limits": {
                "rounds": MAX_ROUNDS, "files": MAX_FILES,
                "file_bytes": MAX_FILE_BYTES, "guidance_bytes": MAX_GUIDANCE_BYTES,
                "tool_result_bytes": MAX_RESULT_BYTES,
            },
            "rounds": self.rounds,
        }

    def receive(self, payload: dict, wire_bytes: int, capture_path: Path) -> dict:
        inventory = self.inventory()
        previous = self.rounds[-1]["wire_bytes"] if self.rounds else None
        row = {
            "round": len(self.rounds) + 1,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "capture_path": str(capture_path),
            "wire_bytes": wire_bytes,
            "wire_delta_bytes": wire_bytes - previous if previous is not None else None,
            "generated_files": inventory,
            "tool_results": [],
        }
        self.rounds.append(row)
        if self.state == "initial":
            if inventory["status"] != "present":
                return self.finish("instructions_" + inventory["status"])
            arguments = {"pattern": "**/*.md", "paths": str(self.instructions)}
            name = advertised_tool(payload, "glob", arguments)
            if name is None:
                return self.finish("unsupported_listing_tool")
            self.pending = {"capture-list": str(self.instructions)}
            self.state = "listing"
            return {"role": "assistant", "content": None, "tool_calls": [
                tool_call("capture-list", name, arguments),
            ]}

        results = {}
        for identifier, path in self.pending.items():
            matches = [
                message for message in payload["messages"]
                if isinstance(message, dict) and message.get("role") == "tool"
                and message.get("tool_call_id") == identifier
            ]
            if len(matches) != 1:
                return self.finish("missing_or_duplicate_tool_result")
            text = text_content(matches[0])
            if text is None:
                return self.finish("unsupported_tool_result")
            results[identifier] = text
            row["tool_results"].append({
                "tool_call_id": identifier, "path": path,
                "decoded_text_bytes": len(text.encode("utf-8")),
            })
        if sum(item["decoded_text_bytes"] for item in row["tool_results"]) > MAX_RESULT_BYTES:
            return self.finish("tool_result_byte_limit")

        if self.state == "listing":
            listing = results["capture-list"].strip()
            if not listing:
                return self.finish("empty_listing")
            if inventory["status"] != "present":
                return self.finish("instructions_" + inventory["status"])
            available = {item["path"]: item for item in inventory["files"]}
            paths = []
            for line in listing.splitlines():
                candidate = Path(line.strip())
                if not candidate.is_absolute():
                    candidate = self.instructions / candidate
                path = str(candidate)
                if path not in available or candidate.resolve() != candidate:
                    return self.finish("unsupported_or_denied_listing")
                if path in paths:
                    return self.finish("duplicate_listing_path")
                paths.append(path)
            if not paths:
                return self.finish("empty_listing")
            if len(paths) > MAX_FILES or any(available[path]["bytes"] > MAX_FILE_BYTES for path in paths):
                return self.finish("guidance_file_limit")
            if sum(available[path]["bytes"] for path in paths) > MAX_GUIDANCE_BYTES:
                return self.finish("guidance_byte_limit")
            calls = []
            self.pending = {}
            for index, path in enumerate(paths):
                arguments = {"path": path, "forceReadLargeFiles": True}
                name = advertised_tool(payload, "view", arguments)
                if name is None:
                    return self.finish("unsupported_read_tool")
                identifier = f"capture-read-{index}"
                self.pending[identifier] = path
                self.selected[path] = available[path]
                calls.append(tool_call(identifier, name, arguments))
            self.state = "reading"
            return {"role": "assistant", "content": None, "tool_calls": calls}

        if self.state != "reading":
            return self.finish("unexpected_round")
        current = {item["path"]: item for item in inventory["files"]}
        for item in row["tool_results"]:
            path = item["path"]
            if inventory["status"] != "present" or current.get(path) != self.selected[path]:
                return self.finish("guidance_changed_during_read")
            try:
                with Path(path).open("rb") as source:
                    raw = source.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES:
                    return self.finish("guidance_file_limit")
                expected = raw.decode("utf-8-sig").replace("\r\n", "\n").rstrip("\n")
            except (OSError, UnicodeError):
                return self.finish("guidance_unavailable")
            observed = results[item["tool_call_id"]].replace("\r\n", "\n")
            # Native view may annotate each line rather than returning raw text.
            unnumbered = re.sub(r"(?m)^(?:L\d+:|\d+[.:]) ?", "", observed)
            body_pattern = r"(?m)^" + re.escape(expected) + r"(?=\n|\Z)"
            matched = bool(expected) and any(
                re.search(body_pattern, text) is not None for text in (observed, unnumbered)
            )
            item["source_body_observed"] = matched
            if not matched:
                return self.finish("read_denied_truncated_or_unmatched")
        return self.finish()
