"""Server-Sent Events parsing -- a pure port of the proxy.mjs SSE accumulator.

Kept dependency-free and side-effect-free so it is straightforward to unit test.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SseEvent:
    event: str
    data: str


def parse_sse_events(body: str) -> list[SseEvent]:
    """Parse an SSE response body into a list of events.

    Mirrors the wire rules used by the original Node proxy: ``event:`` and
    ``data:`` fields, ``:`` comment lines ignored, blank line terminates an
    event, multiple ``data:`` lines joined with newlines, and a trailing event
    without a final blank line is still emitted.
    """
    events: list[SseEvent] = []
    event_type = ""
    data_lines: list[str] = []

    for raw_line in body.split("\n"):
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line

        if line == "":
            if data_lines:
                events.append(SseEvent(event_type or "message", "\n".join(data_lines)))
            event_type = ""
            data_lines = []
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())

    if data_lines:
        events.append(SseEvent(event_type or "message", "\n".join(data_lines)))
    return events
