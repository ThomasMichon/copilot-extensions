"""Collapse/expand rendering of an agent-bridge event stream.

A host agent that delegates to a remote agent wants a *continuous, low-noise*
feed of progress -- not the full chain-of-thought and every tool's sub-output
(which would pollute its context, defeating the point of delegating).

``StreamRenderer`` turns the raw SSE event stream into host-facing text:

- **agent messages** stream in full (this is the signal);
- **chain-of-thought** collapses to a single ``thinking…`` marker per burst;
- **tool calls** collapse to a one-line ``> running: <title> … done`` marker.

On demand, the host can expand thoughts and/or tool content for a range of
events (e.g. ``read --expand all``) without that verbosity ever entering the
default feed.

The renderer is intentionally pure: ``render_event`` takes an event and returns
the text to emit (``""`` when nothing should be shown). This keeps it trivially
unit-testable and reusable by ``send``, ``wait``, and ``read``.
"""

from __future__ import annotations

from typing import Any

ANSI_DIM = "\033[2m"
ANSI_RESET = "\033[0m"

# Tool-call statuses that mean the call has finished (one way or another).
_TERMINAL_TOOL_STATUS = frozenset(
    {"completed", "complete", "success", "succeeded", "failed", "error", "cancelled",
     "canceled"}
)

# Human words for terminal statuses shown in the collapsed marker.
_STATUS_WORD = {
    "completed": "done",
    "complete": "done",
    "success": "done",
    "succeeded": "done",
    "failed": "failed",
    "error": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}

# Marker glyphs (ASCII fallbacks chosen to be safe on any terminal).
_TOOL_MARKER = "\u25b8"  # ▸
_THINK_MARKER = "\u25b8"  # ▸


def _is_terminal_status(status: str | None) -> bool:
    return bool(status) and status.lower() in _TERMINAL_TOOL_STATUS


class StreamRenderer:
    """Stateful renderer converting events into a collapsed (or expanded) feed.

    Construct one per stream consumer. Feed it events in order via
    ``render_event``; the returned strings, concatenated, form the feed.
    """

    def __init__(
        self,
        *,
        expand_thoughts: bool = False,
        expand_tools: bool = False,
        color: bool = True,
    ) -> None:
        self.expand_thoughts = expand_thoughts
        self.expand_tools = expand_tools
        self.color = color
        # Whether we are mid chain-of-thought burst (collapsed mode only).
        self._thinking = False
        # tool_call_id of a tool whose collapsed line is left "open" (no
        # trailing newline yet, awaiting its terminal "… done").
        self._open_tool: str | None = None
        self._titles: dict[str, str] = {}

    # -- helpers -------------------------------------------------------------

    def _dim(self, text: str) -> str:
        return f"{ANSI_DIM}{text}{ANSI_RESET}" if self.color else text

    def _close_open(self) -> str:
        """Terminate any dangling open tool line with a newline."""
        if self._open_tool is not None:
            self._open_tool = None
            return "\n"
        return ""

    # -- main entrypoint -----------------------------------------------------

    def render_event(self, event_type: str, data: dict[str, Any]) -> str:
        """Render a single event into feed text (``""`` if nothing to show)."""
        if event_type == "agent_message":
            return self._render_message(data)
        if event_type == "agent_thought":
            return self._render_thought(data)
        if event_type == "tool_call_start":
            return self._render_tool_start(data)
        if event_type == "tool_call_update":
            return self._render_tool_update(data)
        if event_type == "plan_update":
            title = data.get("title", "")
            if not title:
                return ""
            return self._close_open() + self._reset_thinking() + (
                f"{_TOOL_MARKER} plan: {title}\n"
            )
        if event_type == "turn_complete":
            out = self._close_open() + self._reset_thinking()
            stop = data.get("stop_reason", "")
            if stop:
                return out + f"\n[<] Turn complete ({stop})\n"
            return out + "\n[<] Turn complete\n"
        if event_type == "error":
            out = self._close_open() + self._reset_thinking()
            msg = data.get("message", "Unknown error")
            return out + f"\n[FAIL] {msg}\n"
        # session_state_changed, usage_update, session_info, permission_* are
        # not part of the default feed -- the streaming engine consumes them.
        return ""

    def render_events(self, events: list[dict[str, Any]]) -> str:
        """Render a list of ``{event, data}`` dicts (e.g. a range read)."""
        parts = [self.render_event(e.get("event", ""), e.get("data", {})) for e in events]
        return "".join(parts)

    def heartbeat_line(self, elapsed_seconds: float) -> str:
        """A transient progress line emitted during quiet periods.

        Render-only -- never persisted to the event log. Lets the host agent
        see the remote is still working rather than perceiving a hang.
        """
        secs = int(elapsed_seconds)
        return self._dim(f"{_TOOL_MARKER} …still working ({secs}s)") + "\n"

    # -- per-event renderers -------------------------------------------------

    def _reset_thinking(self) -> str:
        self._thinking = False
        return ""

    def _render_message(self, data: dict[str, Any]) -> str:
        text = data.get("text", "")
        prefix = self._close_open()
        self._thinking = False
        if not text:
            return prefix
        return prefix + text

    def _render_thought(self, data: dict[str, Any]) -> str:
        text = data.get("text", "")
        if self.expand_thoughts:
            prefix = self._close_open()
            if not text:
                return prefix
            return prefix + self._dim(text)
        # Collapsed: one marker per thinking burst, suppress the rest.
        if self._thinking:
            return ""
        prefix = self._close_open()
        self._thinking = True
        return prefix + self._dim(f"{_THINK_MARKER} thinking…") + "\n"

    def _render_tool_start(self, data: dict[str, Any]) -> str:
        tool_id = data.get("tool_call_id", "")
        title = data.get("title") or data.get("kind") or "tool"
        if tool_id:
            self._titles[tool_id] = title
        prefix = self._close_open()
        self._thinking = False
        if self.expand_tools:
            return prefix + f"{_TOOL_MARKER} {title}\n"
        # Collapsed: open a dangling line to be closed by the terminal update.
        self._open_tool = tool_id or "?"
        return prefix + f"{_TOOL_MARKER} running: {title} …"

    def _render_tool_update(self, data: dict[str, Any]) -> str:
        status = data.get("status")
        tool_id = data.get("tool_call_id", "")
        terminal = _is_terminal_status(status)

        if self.expand_tools:
            if not terminal:
                return ""
            out = self._close_open()
            title = self._titles.get(tool_id, "tool")
            out += f"  \u21b3 {title}: {status}\n"
            for line in data.get("content", []) or []:
                out += self._dim(f"    {line}") + "\n"
            return out

        # Collapsed mode.
        if not terminal:
            return ""
        word = _STATUS_WORD.get((status or "").lower(), status or "done")
        if self._open_tool is not None and self._open_tool == (tool_id or "?"):
            # Close the dangling line inline -> "▸ running: <title> … done".
            self._open_tool = None
            return f" {word}\n"
        # No matching open line (e.g. resumed mid-tool); emit a fresh marker.
        prefix = self._close_open()
        title = self._titles.get(tool_id, "tool")
        return prefix + f"{_TOOL_MARKER} running: {title} … {word}\n"
