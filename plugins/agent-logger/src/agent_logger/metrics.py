"""Evidence-based duration and turn counts for an ordered chain of sessions.

``agent-logger metrics --json`` measures one task carried by an ordered chain
of sessions -- a session plus the successors it handed off to -- from their
event logs alone: when the task was kicked off, how long it took on the wall
clock, how much of that the agent spent working (time spent waiting on the
user -- for an ``ask_user`` answer or a permission decision -- is excluded),
and how many turns the user and the agent took.

Sessions resolve through :func:`agent_logger.cold_store.resolve_session` and
are read with :func:`agent_logger.sessions.read_member`, so live, archived and
synced sessions measure the same. Only event types, timestamps, tool names,
tool-call, sub-agent and permission-request ids, and prompt origin/delivery
are read -- never message or tool contents -- and the report carries counts
and UTC times only.

Incompleteness is explicit. When any requested session cannot be resolved or
read, the whole report is ``unknown`` and every metrics block is ``null``: a
partial chain would understate the task. A value the logs cannot establish is
``null``, never ``0``. Correlating a report with a pull request, work item or
any other artifact is the caller's job.
"""

from __future__ import annotations

import io
import json
import re
import tarfile
import zipfile
import zlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from agent_logger import cold_store, sessions
from agent_logger.sessions import SessionRef

SCHEMA_VERSION = 1

_RELEVANT = (
    "user.message",
    "human_response.recorded",
    "assistant.turn_start",
    "assistant.turn_end",
    "tool.execution_start",
    "tool.execution_complete",
    "abort",
    "permission.requested",
    "permission.completed",
    "session.start",
    "session.resume",
    "subagent.completed",
    "subagent.failed",
)
# Markers only open or close spans; they are not activity, so they never move
# a session's first or last event.
_MARKERS = ("permission", "permission_done", "boundary", "agent_done")
# An agent's own events that end its waits when no answer or decision did.
_WAIT_ENDS = ("prompt", "turn_start", "turn_end", "abort")
# Every events.jsonl line starts with its type, so a prefix check skips the
# (possibly very large) irrelevant lines without parsing them.
_PREFIX = b'{"type":"'
_RELEVANT_PREFIXES = tuple(_PREFIX + name.encode() + b'"' for name in _RELEVANT)
_COMPLETE_PREFIX = _PREFIX + b'tool.execution_complete"'
# Characters JSON never escapes, so such an id appears verbatim in its line.
_PLAIN_CALL_ID = re.compile(r"[A-Za-z0-9_.:-]+")
_TIMESTAMP_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?"
    r"\s*(Z|[+-]\d{2}:?\d{2})?$",
    re.IGNORECASE | re.ASCII,
)
_READ_ERRORS = (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile, zlib.error, EOFError)
_COUNTERS = (
    "human_prompts",
    "steering_prompts",
    "ask_user_answers",
    "automated_prompts",
    "agent_turns",
    "subagent_turns",
    "tool_calls",
    "subagent_tool_calls",
    "user_interrupts",
)

Span = tuple[datetime, datetime]


class SessionMetrics(TypedDict):
    kickoff: str | None
    kickoff_origin: str | None
    end: str | None
    end_basis: str | None
    wall_clock_seconds: int | None
    working_seconds: int
    user_turns: int
    human_prompts: int
    steering_prompts: int
    ask_user_answers: int
    automated_prompts: int
    agent_turns: int
    subagent_turns: int
    tool_calls: int
    subagent_tool_calls: int
    user_interrupts: int
    open_turn_at_end: bool


class AggregateMetrics(SessionMetrics):
    sessions_in_window: int


class SessionReport(TypedDict):
    session_id: str
    status: str
    archived: bool | None
    malformed_lines: int | None
    unparseable_timestamps: int | None
    metrics: SessionMetrics | None


class MetricsReport(TypedDict):
    schema_version: int
    until: str | None
    status: str
    unknown_reason: str | None
    unknown_sessions: list[str]
    aggregate: AggregateMetrics | None
    sessions: list[SessionReport]


@dataclass(frozen=True)
class Event:
    """One measured event: what happened and when, never what was said."""

    ts: datetime
    # prompt | answer | ask_done | turn_start | turn_end | tool | abort, or a
    # marker: permission | permission_done (a permission request and its
    # decision) | boundary (a process started the session) | agent_done
    kind: str
    agent: str = ""  # "" for the main agent, else the sub-agent's id
    origin: str = ""  # prompts only: human | agent | schedule | other
    steering: bool = False
    asks_user: bool = False
    reason: str = ""  # aborts only
    # Tool calls and the answers that resolve them; a permission's request id
    call_id: str = ""

    @property
    def sub(self) -> bool:
        return bool(self.agent)


# --- reading ------------------------------------------------------------------


def parse_timestamp(value: object) -> datetime | None:
    """Parse an ISO 8601 timestamp into an aware UTC datetime, or ``None``.

    A timestamp without an offset is taken as UTC. Anything malformed --
    including an out-of-range date, time or offset, or one whose UTC
    conversion overflows -- is ``None``, never an exception.
    """
    if not isinstance(value, str):
        return None
    match = _TIMESTAMP_RE.match(value.strip())
    if not match:
        return None
    year, month, day, hour, minute = (int(match.group(i)) for i in range(1, 6))
    second = int(match.group(6) or 0)
    micro = int((match.group(7) or "0")[:6].ljust(6, "0"))
    zone = match.group(8)
    if zone is None or zone.upper() == "Z":
        offset = timezone.utc
    else:
        digits = zone[1:].replace(":", "")
        hours, minutes = int(digits[:2]), int(digits[2:])
        if hours > 23 or minutes > 59:
            return None
        delta = timedelta(hours=hours, minutes=minutes)
        offset = timezone(-delta if zone[0] == "-" else delta)
    try:
        stamp = datetime(year, month, day, hour, minute, second, micro, tzinfo=offset)
        return stamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _origin(source: object) -> str:
    if not source:
        return "human"
    prefix = str(source).split("-", 1)[0]
    return prefix if prefix in ("agent", "schedule") else "other"


def _classify(record: dict) -> dict[str, object] | None:
    """Return the :class:`Event` fields other than ``ts`` of a measured record."""
    kind = record.get("type")
    if kind not in _RELEVANT:
        return None
    data = record.get("data")
    if not isinstance(data, dict):
        data = {}
    # Sub-agent events carry a top-level agentId; older logs only name the
    # tool call that launched the sub-agent.
    agent = str(record.get("agentId") or data.get("parentToolCallId") or "")
    call = str(data.get("toolCallId") or "")
    if kind == "user.message":
        if agent:
            return None  # a sub-agent's prompt; its work counts through its turns
        return {
            "kind": "prompt",
            "origin": _origin(data.get("source")),
            "steering": data.get("delivery") == "steering",
        }
    if kind == "human_response.recorded":
        response = data.get("response")
        if isinstance(response, dict) and response.get("responseKind") == "ask_user":
            return {"kind": "answer", "agent": agent, "call_id": call}
        return None
    if kind == "assistant.turn_start":
        return {"kind": "turn_start", "agent": agent}
    if kind == "assistant.turn_end":
        return {"kind": "turn_end", "agent": agent}
    if kind == "tool.execution_start":
        asks_user = data.get("toolName") == "ask_user"
        return {"kind": "tool", "agent": agent, "asks_user": asks_user, "call_id": call}
    if kind == "tool.execution_complete":
        # Matters only when it resolves an ask_user call (see _resolve_asks): a
        # successful one is the user's answer, a failed one just ends the wait.
        if not call:
            return None
        resolved = "answer" if data.get("success") is True else "ask_done"
        return {"kind": resolved, "agent": agent, "call_id": call}
    if kind in ("permission.requested", "permission.completed"):
        # A request and its decision pair up by request id.
        request_id = str(data.get("requestId") or "")
        if not request_id:
            return None
        marker = "permission" if kind == "permission.requested" else "permission_done"
        return {"kind": marker, "agent": agent, "call_id": request_id}
    if kind in ("session.start", "session.resume"):
        return {"kind": "boundary"}
    if kind in ("subagent.completed", "subagent.failed"):
        # Keyed like the sub-agent's own turns: by agentId or, in older logs,
        # by the tool call that launched it.
        agent_id = str(record.get("agentId") or "")
        if not (agent_id or call):
            return None
        return {"kind": "agent_done", "agent": agent_id or call, "call_id": call}
    return {"kind": "abort", "agent": agent, "reason": str(data.get("reason") or "")}


def to_event(record: object) -> Event | None:
    """Normalize one ``events.jsonl`` record, or ``None`` when it is not measured.

    A measured record whose timestamp cannot be parsed is ``None`` too.
    """
    if not isinstance(record, dict):
        return None
    fields = _classify(record)
    if fields is None:
        return None
    stamp = parse_timestamp(record.get("timestamp"))
    return None if stamp is None else Event(stamp, **fields)


def _needle(call_id: str) -> bytes:
    """Bytes a completion line must contain to resolve ``call_id``.

    An id JSON could escape cannot be searched for verbatim, so it gets the
    empty needle, which every line contains: its completions are all parsed.
    """
    return call_id.encode() if _PLAIN_CALL_ID.fullmatch(call_id) else b""


def read_events(lines: Iterable[bytes]) -> tuple[list[Event], int, int]:
    """Return one session's measured events in time order.

    Also returns how many lines were malformed (not a JSON object) and how many
    measured records had an unparseable timestamp; both are skipped, never
    guessed at.
    """
    events: list[Event] = []
    malformed = unparseable = 0
    open_asks: dict[str, bytes] = {}
    for raw in lines:
        if raw.startswith(_PREFIX):
            if not raw.startswith(_RELEVANT_PREFIXES):
                continue
            # Tool completions are frequent and can be large; parse one only
            # when it may resolve an open ask_user call.
            if raw.startswith(_COMPLETE_PREFIX) and not any(
                needle in raw for needle in open_asks.values()
            ):
                continue
        elif not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except (ValueError, RecursionError):
            malformed += 1
            continue
        if not isinstance(record, dict):
            malformed += 1
            continue
        fields = _classify(record)
        if fields is None:
            continue
        call_id = str(fields.get("call_id") or "")
        if record.get("type") == "tool.execution_complete" and call_id not in open_asks:
            continue
        stamp = parse_timestamp(record.get("timestamp"))
        if stamp is None:
            unparseable += 1
            continue
        event = Event(stamp, **fields)
        if event.kind == "tool" and event.asks_user and call_id:
            open_asks[call_id] = _needle(call_id)
        elif event.kind in ("answer", "ask_done") and call_id:
            open_asks.pop(call_id, None)
        events.append(event)
    events.sort(key=lambda event: event.ts)
    return _resolve_asks(events), malformed, unparseable


def _resolve_asks(events: list[Event]) -> list[Event]:
    """Keep only the first answer or completion that resolves each ask_user call.

    A question can be resolved by both a recorded answer and its tool
    completion; matching on the tool-call id counts it once and drops the
    completions of every other tool. An answer without an id (older logs)
    resolves the oldest open question.
    """
    open_calls: dict[str, None] = {}  # insertion-ordered, oldest first
    kept: list[Event] = []
    for event in events:
        if event.kind in ("answer", "ask_done"):
            if event.call_id:
                if event.call_id not in open_calls:
                    continue
                del open_calls[event.call_id]
            elif open_calls:
                oldest = next(iter(open_calls))
                del open_calls[oldest]
                event = replace(event, call_id=oldest)
        elif event.kind == "tool" and event.asks_user and event.call_id:
            open_calls[event.call_id] = None
        kept.append(event)
    return kept


# --- measuring ----------------------------------------------------------------


def session_intervals(events: list[Event]) -> tuple[list[Span], list[Span], list[Span], bool]:
    """Return one session's (main busy, sub-agent busy, waiting-on-user) spans.

    Busy time is each model turn, plus the processing between a prompt and the
    first main turn it triggers (prompt hooks). A sub-agent's turn ends no
    later than the sub-agent completes or fails.

    An agent waits on the user during an ``ask_user`` call until its answer or
    completion (matched by tool-call id), and during a permission request until
    its decision (matched by request id). Failing a match, a wait ends at the
    agent's next turn boundary, prompt or abort, or when the sub-agent ends; a
    parallel tool call does not end it. Each sub-agent's busy spans come back
    net of its own waits, and the returned waits -- every agent's -- are for
    the caller to remove from the main agent's busy time: its turn stays open
    while a sub-agent it launched runs, so a sub-agent held up on the user
    holds it up too.

    Each process that ran the session is measured on its own: whatever one
    left open -- a turn, a wait, a prompt no turn took up -- is closed at its
    last measured event, never extrapolated, so the time a session lay dead
    before it was resumed is not counted. The returned flag tells whether the
    log ends inside an open turn.
    """
    open_turns: dict[str, datetime] = {}
    main_busy: list[Span] = []
    sub_busy: dict[str, list[Span]] = {}
    waits: list[Span] = []
    sub_waits: dict[str, list[Span]] = {}
    pending_prompt: datetime | None = None
    # ("ask", tool-call id) or ("permission", request id) -> (agent, start)
    pending_waits: dict[tuple[str, str], tuple[str, datetime]] = {}
    last: datetime | None = None  # the current process's last measured event

    def busy(agent: str, start: datetime, end: datetime) -> None:
        if agent:
            sub_busy.setdefault(agent, []).append((start, end))
        else:
            main_busy.append((start, end))

    def wait(agent: str, start: datetime, end: datetime) -> None:
        waits.append((start, end))
        if agent:
            sub_waits.setdefault(agent, []).append((start, end))

    def end_wait(key: tuple[str, str], at: datetime) -> None:
        pending = pending_waits.pop(key, None)
        if pending is not None:
            wait(pending[0], pending[1], at)

    def end_waits(agent: str, at: datetime) -> None:
        for key, (owner, start) in list(pending_waits.items()):
            if owner == agent:
                wait(owner, start, at)
                del pending_waits[key]

    def close_open(at: datetime) -> None:
        for agent, start in open_turns.items():
            busy(agent, start, at)
        for owner, start in pending_waits.values():
            wait(owner, start, at)
        open_turns.clear()
        pending_waits.clear()

    for event in events:
        if event.kind == "boundary":
            if last is not None:
                close_open(last)
            pending_prompt = None
            continue
        if event.kind == "agent_done":
            for key in {event.agent, event.call_id} - {""}:
                start = open_turns.pop(key, None)
                if start is not None:
                    busy(key, start, event.ts)
                end_waits(key, event.ts)
            continue
        if event.kind == "permission":
            pending_waits.setdefault(("permission", event.call_id), (event.agent, event.ts))
            continue
        if event.kind == "permission_done":
            end_wait(("permission", event.call_id), event.ts)
            continue
        last = event.ts
        if event.kind in ("answer", "ask_done"):
            end_wait(("ask", event.call_id), event.ts)
        elif event.kind in _WAIT_ENDS:
            end_waits(event.agent, event.ts)
        if event.kind == "prompt":
            # A prompt steered into an open main turn is absorbed by that work.
            if pending_prompt is None and "" not in open_turns:
                pending_prompt = event.ts
        elif event.kind == "turn_start":
            if event.agent in open_turns:
                busy(event.agent, open_turns[event.agent], event.ts)
            open_turns[event.agent] = event.ts
            if not event.sub and pending_prompt is not None:
                main_busy.append((pending_prompt, event.ts))
                pending_prompt = None
        elif event.kind == "turn_end":
            start = open_turns.pop(event.agent, None)
            if start is not None:
                busy(event.agent, start, event.ts)
        elif event.kind == "abort" and not event.sub:
            start = open_turns.pop("", None)
            if start is not None:
                main_busy.append((start, event.ts))
            pending_prompt = None
        elif event.kind == "tool" and event.asks_user:
            pending_waits.setdefault(("ask", event.call_id), (event.agent, event.ts))
    open_at_end = bool(open_turns)
    if last is not None:
        close_open(last)
    sub_net = [
        span
        for agent, spans in sub_busy.items()
        for span in _subtract(_union(spans), _union(sub_waits.get(agent, [])))
    ]
    return main_busy, sub_net, waits, open_at_end


def _activity(events: Sequence[Event]) -> tuple[datetime, datetime] | None:
    """The first and last event times, ignoring lifecycle markers."""
    first = next((event.ts for event in events if event.kind not in _MARKERS), None)
    last = next((event.ts for event in reversed(events) if event.kind not in _MARKERS), None)
    return None if first is None or last is None else (first, last)


def _clip(spans: Iterable[Span], low: datetime, high: datetime) -> list[Span]:
    clipped = []
    for start, end in spans:
        start, end = max(start, low), min(end, high)
        if end > start:
            clipped.append((start, end))
    return clipped


def _union(spans: Iterable[Span]) -> list[Span]:
    merged: list[Span] = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _subtract(base: list[Span], cut: list[Span]) -> list[Span]:
    """Remove the sorted, disjoint ``cut`` spans from the sorted, disjoint ``base``."""
    result = []
    first = 0
    for start, end in base:
        cursor = start
        while first < len(cut) and cut[first][1] <= cursor:
            first += 1
        index = first
        while index < len(cut) and cut[index][0] < end and cursor < end:
            cut_start, cut_end = cut[index]
            if cut_start > cursor:
                result.append((cursor, cut_start))
            cursor = max(cursor, cut_end)
            index += 1
        if cursor < end:
            result.append((cursor, end))
    return result


def _measure(spans: Iterable[Span]) -> timedelta:
    return sum((end - start for start, end in spans), timedelta())


def _seconds(duration: timedelta) -> int:
    """Whole seconds, rounded down so a measurement never overstates."""
    return max(0, duration // timedelta(seconds=1))


def _iso(stamp: datetime) -> str:
    utc = stamp.astimezone(timezone.utc)
    return utc.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _count(events: Iterable[Event]) -> dict[str, int]:
    counts = dict.fromkeys(_COUNTERS, 0)
    for event in events:
        if event.kind == "prompt":
            if event.origin == "human":
                counts["human_prompts"] += 1
                counts["steering_prompts"] += int(event.steering)
            else:
                counts["automated_prompts"] += 1
        elif event.kind == "answer":
            counts["ask_user_answers"] += 1
        elif event.kind == "turn_start":
            counts["subagent_turns" if event.sub else "agent_turns"] += 1
        elif event.kind == "tool":
            counts["subagent_tool_calls" if event.sub else "tool_calls"] += 1
        elif event.kind == "abort" and event.reason == "user_initiated":
            counts["user_interrupts"] += 1
    return counts


def _block(
    kickoff: Event | None,
    end: datetime | None,
    end_basis: str | None,
    working: timedelta,
    counts: dict[str, int],
    open_turn: bool,
) -> SessionMetrics:
    wall = None if kickoff is None or end is None else _seconds(end - kickoff.ts)
    return {
        "kickoff": None if kickoff is None else _iso(kickoff.ts),
        "kickoff_origin": None if kickoff is None else kickoff.origin,
        "end": None if end is None else _iso(end),
        "end_basis": end_basis,
        "wall_clock_seconds": wall,
        "working_seconds": _seconds(working),
        "user_turns": counts["human_prompts"] + counts["ask_user_answers"],
        "human_prompts": counts["human_prompts"],
        "steering_prompts": counts["steering_prompts"],
        "ask_user_answers": counts["ask_user_answers"],
        "automated_prompts": counts["automated_prompts"],
        "agent_turns": counts["agent_turns"],
        "subagent_turns": counts["subagent_turns"],
        "tool_calls": counts["tool_calls"],
        "subagent_tool_calls": counts["subagent_tool_calls"],
        "user_interrupts": counts["user_interrupts"],
        "open_turn_at_end": open_turn,
    }


@dataclass
class _Loaded:
    session_id: str
    status: str  # ok | missing | invalid_id | unreadable
    archived: bool | None = None
    events: list[Event] = field(default_factory=list)
    malformed: int | None = None
    unparseable: int | None = None


def _load(session_id: str, resolve: Callable[[str], SessionRef | None]) -> _Loaded:
    if not cold_store._is_safe_session_id(session_id):
        return _Loaded(session_id, "invalid_id")
    try:
        ref = resolve(session_id)
    except _READ_ERRORS:
        return _Loaded(session_id, "unreadable")
    if ref is None:
        return _Loaded(session_id, "missing")
    try:
        data = sessions.read_member(ref, sessions.EVENTS_MEMBER)
    except _READ_ERRORS:
        data = None
    if data is None:
        return _Loaded(session_id, "unreadable", archived=ref.is_archive)
    events, malformed, unparseable = read_events(io.BytesIO(data))
    return _Loaded(session_id, "ok", ref.is_archive, events, malformed, unparseable)


@dataclass(frozen=True)
class _Window:
    kickoff: Event
    end: datetime
    end_basis: str  # until | last_event


def _window(loaded: Sequence[_Loaded], until: datetime | None) -> _Window | None:
    """Kickoff is the first human prompt (else the first prompt) up to ``until``.

    The window ends at ``until`` when a log continues past it, else at the
    last measured event: it is never extended past the evidence.
    """
    prompts = [
        event
        for session in loaded
        for event in session.events
        if event.kind == "prompt" and (until is None or event.ts <= until)
    ]
    candidates = [event for event in prompts if event.origin == "human"] or prompts
    if not candidates:
        return None
    kickoff = min(candidates, key=lambda event: event.ts)
    activity = [_activity(session.events) for session in loaded]
    last = max(bounds[1] for bounds in activity if bounds is not None)
    if until is not None and last > until:
        return _Window(kickoff, until, "until")
    return _Window(kickoff, last, "last_event")


def _session_metrics(events: list[Event], window: _Window) -> tuple[SessionMetrics, list[Span]]:
    """One session's metrics within ``window`` and its working spans."""
    low = window.kickoff.ts
    bounds = _activity(events)
    if bounds is None or bounds[0] > window.end or bounds[1] < low:
        return _block(None, None, None, timedelta(), dict.fromkeys(_COUNTERS, 0), False), []
    last = bounds[1]
    end, basis = (window.end, "until") if last > window.end else (last, "last_event")
    in_window = [event for event in events if low <= event.ts <= end]
    kickoff = next((event for event in in_window if event.kind == "prompt"), None)
    main_busy, sub_busy, waits, open_at_end = session_intervals(events)
    main = _subtract(_union(_clip(main_busy, low, end)), _union(_clip(waits, low, end)))
    spans = _union(main + _clip(sub_busy, low, end))
    open_turn = basis == "last_event" and open_at_end
    return _block(kickoff, end, basis, _measure(spans), _count(in_window), open_turn), spans


def measure_sessions(
    session_ids: Sequence[str],
    *,
    until: datetime | None = None,
    resolve: Callable[[str], SessionRef | None] | None = None,
) -> MetricsReport:
    """Measure the task carried by ``session_ids``, ordered oldest first.

    ``until`` cuts the measurement off (a naive value is UTC): later events are
    ignored and prompts after it cannot be the kickoff. ``resolve`` maps a
    session id to a :class:`~agent_logger.sessions.SessionRef` and defaults to
    :func:`agent_logger.cold_store.resolve_session`. Repeated ids are measured
    once. The report's ``status`` is ``measured``, or ``unknown`` with every
    metrics block ``null`` when a session is unavailable or no prompt is found.
    """
    ids = list(dict.fromkeys(session_ids))
    if not ids:
        raise ValueError("at least one session id is required")
    if until is not None:
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        else:
            until = until.astimezone(timezone.utc)
    loaded = [_load(session_id, resolve or cold_store.resolve_session) for session_id in ids]
    rows: list[SessionReport] = [
        {
            "session_id": session.session_id,
            "status": session.status,
            "archived": session.archived,
            "malformed_lines": session.malformed,
            "unparseable_timestamps": session.unparseable,
            "metrics": None,
        }
        for session in loaded
    ]
    report: MetricsReport = {
        "schema_version": SCHEMA_VERSION,
        "until": None if until is None else _iso(until),
        "status": "unknown",
        "unknown_reason": None,
        "unknown_sessions": [],
        "aggregate": None,
        "sessions": rows,
    }
    unavailable = [session.session_id for session in loaded if session.status != "ok"]
    if unavailable:
        report["unknown_reason"] = "session_unavailable"
        report["unknown_sessions"] = unavailable
        return report
    window = _window(loaded, until)
    if window is None:
        report["unknown_reason"] = "no_kickoff"
        return report

    counts = dict.fromkeys(_COUNTERS, 0)
    spans: list[Span] = []
    in_window = 0
    open_turn = False
    for session, row in zip(loaded, rows, strict=True):
        metrics, session_spans = _session_metrics(session.events, window)
        row["metrics"] = metrics
        if metrics["end"] is None:
            continue
        in_window += 1
        spans += session_spans
        open_turn = open_turn or metrics["open_turn_at_end"]
        for name in _COUNTERS:
            counts[name] += metrics[name]
    block = _block(
        window.kickoff, window.end, window.end_basis, _measure(_union(spans)), counts, open_turn
    )
    report["aggregate"] = {**block, "sessions_in_window": in_window}
    report["status"] = "measured"
    return report
