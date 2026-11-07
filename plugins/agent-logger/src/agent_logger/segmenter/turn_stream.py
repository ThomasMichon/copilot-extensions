"""Lazy turn iteration over a Copilot session's ``events.jsonl``.

:class:`TurnStream` reads the transcript one line at a time and yields each
conversation turn once it is complete, so memory stays bounded by a single
turn rather than by the transcript size. :func:`parse_events` materializes the
same turns for callers that need them all at once.

:mod:`agent_logger.segmenter.collate` re-exports these names for existing
callers.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agent_logger.secret_patterns import redact

# Event types to skip entirely — no useful log content
SKIP_EVENT_TYPES = {
    "hook.start",
    "hook.end",
}

# Tool calls that are noise for logging purposes
SKIP_TOOL_NAMES = {
    "report_intent",
}


def truncate(text: str, limit: int) -> str:
    """Truncate to *limit* chars, appending a note if trimmed."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text)} total chars]"


def strip_system_tags(text: str) -> str:
    """Remove <current_datetime>, <reminder>, and similar system wrapper tags."""
    # Remove known system-injected blocks
    text = re.sub(
        r'<current_datetime>.*?</current_datetime>\s*',
        '', text, flags=re.DOTALL,
    )
    text = re.sub(
        r'<reminder>.*?</reminder>\s*',
        '', text, flags=re.DOTALL,
    )
    return text.strip()


class TurnStream:
    """Lazily parse ``events.jsonl`` into turns, one turn at a time.

    Iterating reads the transcript line by line and yields each turn dict once
    it is complete (when the next turn starts, or at the end of the window), so
    neither the raw events nor the earlier turns are retained. Tool results are
    truncated to ``max_tool_output`` as they are attached.

    ``session_start`` is updated in place while iterating; read it after the
    iteration finishes for the final session metadata. ``turn_count`` is the
    number of turns yielded so far. Each iteration re-reads the file.
    """

    def __init__(
        self,
        session_dir: Path,
        cutoff: str | None,
        max_tool_output: int,
        since: str | None = None,
    ) -> None:
        self.events_file = session_dir / "events.jsonl"
        self.cutoff = cutoff
        self.max_tool_output = max_tool_output
        self.since = since
        self.session_start: dict[str, Any] = {}
        self.turn_count = 0

    def __iter__(self) -> Iterator[dict[str, Any]]:
        self.session_start.clear()
        self.turn_count = 0
        for turn in self._turns():
            self.turn_count += 1
            yield turn

    def _turns(self) -> Iterator[dict[str, Any]]:
        if not self.events_file.exists():
            return

        since = self.since
        cutoff = self.cutoff
        max_tool_output = self.max_tool_output
        current_turn: dict[str, Any] | None = None

        with open(self.events_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue

                evt_type = evt.get("type", "")
                data = evt.get("data", {})
                timestamp = evt.get("timestamp", "")

                # Time window checks
                if since and timestamp and timestamp < since:
                    # Still parse session.start for metadata even if before window
                    if evt_type != "session.start":
                        continue
                if cutoff and timestamp and timestamp > cutoff:
                    break

                if evt_type in SKIP_EVENT_TYPES:
                    continue

                if evt_type == "session.start":
                    self.session_start.clear()
                    self.session_start.update({
                        "session_id": data.get("sessionId", ""),
                        "version": data.get("copilotVersion", ""),
                        "start_time": data.get("startTime", ""),
                        "cwd": data.get("context", {}).get("cwd", ""),
                        "git_root": data.get("context", {}).get("gitRoot", ""),
                        "branch": data.get("context", {}).get("branch", ""),
                        "head_commit": data.get("context", {}).get("headCommit", ""),
                    })

                elif evt_type == "user.message":
                    # A new turn starts; the previous one can no longer change.
                    if current_turn is not None:
                        yield current_turn
                    content = data.get("content", "")
                    content = strip_system_tags(content)
                    current_turn = {
                        "user_message": redact(content),
                        "timestamp": timestamp,
                        "assistant_messages": [],
                        "tool_calls": [],
                    }

                elif evt_type == "assistant.turn_start":
                    if current_turn is None:
                        current_turn = {
                            "user_message": "",
                            "timestamp": timestamp,
                            "assistant_messages": [],
                            "tool_calls": [],
                        }

                elif evt_type == "assistant.message":
                    if current_turn is None:
                        continue

                    # Extract reasoning/chain-of-thought (when available in plaintext)
                    reasoning = data.get("reasoningText", "")
                    if reasoning and reasoning.strip():
                        current_turn["assistant_messages"].append(
                            f"*[Reasoning]:* {redact(reasoning)}"
                        )

                    # Extract prose content (non-tool-call text)
                    content = data.get("content", "")
                    if content and content.strip():
                        current_turn["assistant_messages"].append(redact(content))

                    # Extract tool requests (names + key args)
                    for tr in data.get("toolRequests", []):
                        tool_name = tr.get("name", "")
                        if tool_name in SKIP_TOOL_NAMES:
                            continue
                        args = tr.get("arguments", {})
                        tool_call_id = tr.get("toolCallId", "")
                        # Summarize args — keep small ones, truncate large
                        args_summary = _summarize_tool_args(tool_name, args)
                        current_turn["tool_calls"].append({
                            "id": tool_call_id,
                            "tool": tool_name,
                            "args": args_summary,
                            "result": None,  # filled by execution_complete
                            "success": None,
                        })

                elif evt_type == "tool.execution_complete":
                    if current_turn is None:
                        continue
                    call_id = data.get("toolCallId", "")
                    success = data.get("success", True)
                    result = data.get("result", {})
                    result_content = (
                        result.get("content", "") if isinstance(result, dict) else str(result)
                    )

                    # Find matching tool call and attach result
                    for tc in current_turn["tool_calls"]:
                        if tc["id"] == call_id:
                            tc["success"] = success
                            tc["result"] = truncate(
                                redact(str(result_content)),
                                max_tool_output,
                            )
                            break

        if current_turn is not None:
            yield current_turn


def parse_events(
    session_dir: Path,
    cutoff: str | None,
    max_tool_output: int,
    since: str | None = None,
) -> dict[str, Any]:
    """Parse events.jsonl into structured sections.

    Returns a dict with keys:
        session_start  — dict of session metadata
        turns          — list of turn dicts (user msg, assistant msgs, tools)

    This materializes every turn; prefer iterating a :class:`TurnStream`
    directly when the turns can be consumed one at a time.
    """
    stream = TurnStream(session_dir, cutoff, max_tool_output, since=since)
    turns = list(stream)
    return {
        "session_start": stream.session_start,
        "turns": turns,
    }


def _summarize_tool_args(tool_name: str, args: dict[str, Any]) -> str:
    """Produce a brief summary of tool call arguments."""
    if not args:
        return ""

    # For shell/powershell commands, show the command
    if tool_name in ("powershell", "bash"):
        cmd = args.get("command", "")
        desc = args.get("description", "")
        if desc:
            return f"{desc}: {truncate(cmd, 200)}"
        return truncate(cmd, 200)

    # For file operations, show the path
    if tool_name in ("view", "create", "edit"):
        path = args.get("path", "")
        if tool_name == "edit":
            old = args.get("old_str", "")
            return f"{path} (edit: {truncate(old, 80)})"
        return path

    # For grep/glob, show the pattern
    if tool_name in ("grep", "glob"):
        pattern = args.get("pattern", "")
        path = args.get("path", "")
        return f"pattern={pattern}" + (f" in {path}" if path else "")

    # For task/sub-agents, show the description
    if tool_name == "task":
        desc = args.get("description", "")
        agent = args.get("agent_type", "")
        return f"{agent}: {desc}"

    # For session_store_sql / sql
    if tool_name in ("session_store_sql", "sql"):
        return args.get("description", "") or truncate(args.get("query", ""), 120)

    # For web_fetch / web_search
    if tool_name in ("web_fetch", "web_search"):
        return args.get("url", "") or args.get("query", "")

    # For skill invocation
    if tool_name == "skill":
        return args.get("skill", "")

    # For ask_user
    if tool_name == "ask_user":
        return truncate(args.get("question", ""), 150)

    # For MCP tools, show the tool name hint and compact args
    compact = json.dumps(args, separators=(",", ":"), default=str)
    return truncate(compact, 200)
