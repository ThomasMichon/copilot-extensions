"""Represent a live *interactive* Copilot CLI session over the bridge's SSE.

Phase 5 of the live-session-messaging effort: the **read** counterpart to the
Phase 1 registration inbox. A registered live interactive session (see
``routes/live_sessions.py``) pushes its Copilot **extension SDK** event stream
to the bridge, which translates those events into the bridge's *existing* event
vocabulary and exposes them over the ordinary ``EventLog`` + SSE machinery -- so
Neuron Forge (and any bridge consumer) can **view a live CLI session** without
the bridge owning the process and without the destructive take-over that is
today the only way NF interacts with an interactive session.

Two deliberate boundaries make this safe and honest:

* **Off the ACP-owned SessionManager.** Represented sessions are NOT bridge
  ``Session`` objects. The ``SessionManager`` drives ACP children
  (reattach/resync/watchdog/idle-reaper); a phantom session there would invite
  that machinery to drive a process the bridge does not own. Represented event
  logs live in a separate in-memory ``LiveEventStore`` keyed by session id.

* **In-memory only (``EventLog(db=None)``).** The ``events`` table has a
  ``FOREIGN KEY -> sessions(id)`` under ``PRAGMA foreign_keys=ON``, so a
  represented id (which has no ``sessions`` row) cannot persist there. Durability
  is unnecessary: NF seeds **cold history from the on-disk transcript** and the
  represented log carries only the **live tail** (honest reduced fidelity). A
  bridge restart simply clears the tail; the extension re-registers and resumes.

The translation is intentionally lower-fidelity than native ACP: streaming
deltas, plans, and raw tool arguments/results are thinned or dropped in favor of
a faithful, safe view. The load-bearing safety line is **permissions**: a
represented ``permission.requested`` is surfaced read-only, carrying *no*
correlation id, so it is structurally unanswerable by a remote viewer --
approval can only ever happen at the operator's terminal.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

from .events import EventLog, SseEvent


def _text(value: Any) -> str | None:
    """Coerce a possibly-missing SDK text field to a non-empty str, or None."""
    if isinstance(value, str) and value:
        return value
    return None


def translate_sdk_event(
    sdk_type: str, data: dict[str, Any] | None
) -> list[tuple[str, dict[str, Any]]]:
    """Map one Copilot extension SDK ``SessionEvent`` to bridge event(s).

    Returns a list of ``(event_type, data)`` pairs in the bridge's existing
    event vocabulary (the same one the ACP path emits, so NF's SSE consumer
    needs no new grammar). Unknown or intentionally-dropped SDK event types
    return ``[]``. Pure and side-effect free -- unit-testable in isolation.

    Reduced-fidelity by design: streaming deltas (``assistant.message_delta`` /
    ``assistant.streaming_delta``) are dropped in favor of the final
    ``assistant.message``; plan operations and most session-level events are
    omitted. A represented ``permission.requested`` carries **no** ``requestId``
    -- the two-writer safety boundary.
    """
    d = data or {}
    # Sub-agent instance id, when present, is passed through so a consumer can
    # attribute nested-agent output without inventing a new event type.
    agent_id = d.get("agentId")

    def _out(payload: dict[str, Any]) -> dict[str, Any]:
        if agent_id:
            payload = {**payload, "agent_id": agent_id}
        return payload

    if sdk_type == "user.message":
        content = _text(d.get("content"))
        if content is None:
            return []
        return [("user_message", _out({"content": content}))]

    if sdk_type == "assistant.message":
        content = _text(d.get("content"))
        if content is None:
            return []
        return [("agent_message", _out({"text": content}))]

    if sdk_type == "assistant.reasoning":
        content = _text(d.get("content"))
        if content is None:
            return []
        return [("agent_thought", _out({"text": content}))]

    if sdk_type == "tool.execution_start":
        tool_call_id = d.get("toolCallId")
        if not tool_call_id:
            return []
        name = d.get("toolName") or "tool"
        if name == "ask_user":
            # The agent has stopped mid-turn to ask the operator a question.
            # Represent it as a first-class, legible request (prompt + offered
            # choices) rather than an opaque tool spinner that never completes
            # -- the exact failure that leaves a represented CLI session looking
            # permanently "Responding…". READ-ONLY here (mirrors
            # ``permission.requested``): this SDK path represents a *live CLI
            # session* whose interactive Copilot owns the reply, so the answer
            # affordance downstream is a take-over, never an inline reply. Kept
            # in step with the NF-side translator
            # (services/neuron-forge/server/core/live_representation.py).
            args = d.get("arguments")
            args = args if isinstance(args, dict) else {}
            return [(
                "ask_user_request",
                _out({
                    "tool_call_id": tool_call_id,
                    "message": _text(args.get("message")),
                    "requested_schema": args.get("requestedSchema"),
                    "read_only": True,
                }),
            )]
        return [(
            "tool_call_start",
            _out({
                "tool_call_id": tool_call_id,
                "title": name,
                "kind": name,
                "raw_input": d.get("arguments"),
            }),
        )]

    if sdk_type == "tool.execution_complete":
        tool_call_id = d.get("toolCallId")
        if not tool_call_id:
            return []
        success = bool(d.get("success"))
        status = "completed" if success else "failed"
        content: list[str] = []
        result = d.get("result")
        if isinstance(result, dict):
            text = _text(result.get("detailedContent")) or _text(
                result.get("content")
            )
            if text is not None:
                content.append(text)
        if not success:
            err = d.get("error")
            if isinstance(err, dict):
                msg = _text(err.get("message"))
                if msg is not None:
                    content.append(msg)
        return [(
            "tool_call_update",
            _out({
                "tool_call_id": tool_call_id,
                "status": status,
                "content": content,
                "raw_output": None,
            }),
        )]

    if sdk_type == "assistant.usage":
        model = d.get("model")
        if not model:
            return []
        return [(
            "usage_update",
            _out({
                "input_tokens": d.get("inputTokens"),
                "output_tokens": d.get("outputTokens"),
                "model": model,
                "context_size": None,
                "context_used": None,
            }),
        )]

    if sdk_type == "session.usage_info":
        current = d.get("currentTokens")
        limit = d.get("tokenLimit")
        if current is None and limit is None:
            return []
        return [(
            "usage_update",
            _out({
                "input_tokens": None,
                "output_tokens": None,
                "model": None,
                "context_size": limit,
                "context_used": current,
            }),
        )]

    if sdk_type == "assistant.turn_end":
        return [("turn_complete", _out({"stop_reason": None}))]

    if sdk_type == "permission.requested":
        # READ-ONLY: deliberately omit ``requestId`` so a remote viewer cannot
        # respond -- approval stays with the human at the terminal. This is the
        # load-bearing two-writer safety line for the read path.
        req = d.get("permissionRequest")
        payload: dict[str, Any] = {"read_only": True}
        if isinstance(req, dict):
            for key in ("kind", "intention", "fullCommandText", "toolCallId"):
                if req.get(key) is not None:
                    payload[key] = req[key]
        return [("permission_request", _out(payload))]

    # Everything else is intentionally not represented (reduced fidelity).
    return []


#: SDK event types that mean "the assistant is actively working a turn."
_TURN_ACTIVITY_TYPES = frozenset({
    "user.message",
    "assistant.message",
    "assistant.reasoning",
    "tool.execution_start",
    "tool.execution_complete",
    "permission.requested",
})
#: SDK event type that ends a turn (the assistant went idle).
_TURN_END_TYPE = "assistant.turn_end"


def derive_turn_state(
    raw_events: list[dict[str, Any]], *, prior_state: str | None = None
) -> tuple[str | None, bool]:
    """Fold a batch of raw SDK events into a coarse ``turn_state``.

    Returns ``(turn_state, saw_activity)`` where ``turn_state`` is ``"running"``
    (a turn is in progress), ``"idle"`` (the last turn ended), or ``prior_state``
    if the batch carried no turn signal. ``saw_activity`` is True when any
    activity event was seen (used to refresh ``last_activity_at``). Pure and
    order-sensitive: the *last* turn signal in the batch wins. This is the
    objective, token-free half of progress legibility (Phase 7 Channel A) --
    ``stalled`` is *not* decided here; it is computed on read from
    ``last_activity_at`` vs. a threshold.
    """
    state = prior_state
    saw_activity = False
    for event in raw_events:
        etype = event.get("type")
        if etype == _TURN_END_TYPE:
            state = "idle"
        elif etype in _TURN_ACTIVITY_TYPES:
            state = "running"
            saw_activity = True
    return state, saw_activity


#: Hard cap on a live-session progress summary -- a status line, not a transcript.
PROGRESS_SUMMARY_MAX = 280
_PROGRESS_PHASE_MAX = 40
_PROGRESS_PR_MAX = 120


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def build_progress_snapshot(
    summary: str,
    *,
    phase: str = "",
    blocker: str | None = None,
    pr: str | None = None,
    ts: float,
) -> dict[str, object]:
    """Build a bounded, latest-only progress snapshot for a live session.

    The live-session analogue of agent-dispatch's dispatched-task progress beat
    (Phase 7 Slice 7c): every free-text field is hard-capped so an operator
    session's beat stays a *status line*, never a chat log.
    """
    snapshot: dict[str, object] = {
        "summary": _clip(summary, PROGRESS_SUMMARY_MAX) or "-",
        "ts": ts,
    }
    phase_c = _clip(phase, _PROGRESS_PHASE_MAX)
    if phase_c:
        snapshot["phase"] = phase_c
    blocker_c = _clip(blocker, PROGRESS_SUMMARY_MAX)
    if blocker_c:
        snapshot["blocker"] = blocker_c
    pr_c = _clip(pr, _PROGRESS_PR_MAX)
    if pr_c:
        snapshot["pr"] = pr_c
    return snapshot


class LiveEventStore:
    """In-memory registry of represented ``EventLog``s, keyed by session id.

    One ``EventLog`` per represented live interactive session, constructed with
    ``db=None`` so events live only in memory (see the module docstring for why
    persistence is neither possible nor needed). Thread-safe for the get/create
    path; ``EventLog`` itself guards appends and SSE reads internally.
    """

    def __init__(self) -> None:
        self._logs: dict[str, EventLog] = {}
        self._lock = Lock()

    def get(self, session_id: str) -> EventLog | None:
        """Return the represented log for ``session_id``, or None if none yet."""
        with self._lock:
            return self._logs.get(session_id)

    def get_or_create(self, session_id: str) -> EventLog:
        """Return (creating if needed) the represented log for ``session_id``."""
        with self._lock:
            log = self._logs.get(session_id)
            if log is None:
                log = EventLog()  # db=None -> in-memory only
                self._logs[session_id] = log
            return log

    def drop(self, session_id: str) -> None:
        """Forget a session's represented log (on deregister) to free memory."""
        with self._lock:
            self._logs.pop(session_id, None)

    def ingest(
        self, session_id: str, sdk_events: list[dict[str, Any]]
    ) -> int:
        """Translate + append a batch of raw SDK events; return the count appended.

        Each item is a raw SDK event ``{"type": str, "data": dict}``. Events that
        translate to nothing (unknown/dropped types) are silently skipped.
        """
        log = self.get_or_create(session_id)
        appended = 0
        for item in sdk_events:
            if not isinstance(item, dict):
                continue
            sdk_type = item.get("type")
            if not isinstance(sdk_type, str):
                continue
            data = item.get("data")
            data = data if isinstance(data, dict) else {}
            for event_type, payload in translate_sdk_event(sdk_type, data):
                log.append(event_type, payload)
                appended += 1
        return appended


# -- D1: read a live session's reply turn from its represented stream --------

# One turn's worth of collected assistant text plus how it ended.
TurnReply = dict[str, Any]


async def await_turn_reply(
    log: EventLog, *, after: int, timeout: float
) -> TurnReply:
    """Wait for the represented session's next reply turn, after event ``after``.

    This is D1's read primitive: a message injected into a live session is
    answered by the receiver's *ordinary* turn, which its extension mirrors into
    the represented stream as ``agent_message`` text bounded by a
    ``turn_complete``. We collect the assistant text produced after ``after`` up
    to (and including) the first ``turn_complete``, then return it -- so a caller
    ``send``-and-waits and reads the answer with no extra protocol.

    Returns ``{"replied": bool, "reply": str | None, "stop_reason": str | None,
    "last_id": int}``. On timeout ``replied`` is False and ``reply`` is whatever
    partial assistant text (if any) had arrived -- the message still sits in the
    durable queue regardless.

    Honest limit (single-operator, deliberate use): this reads the *next* turn
    to complete after ``after``. If an unrelated turn was already in flight when
    the caller sent, that turn's completion is what returns first; correlating a
    specific reply to a specific ``msg-id`` is a later refinement (the envelope
    ``msg-id`` is the seed).
    """
    deadline = time.monotonic() + timeout
    cursor = after
    texts: list[str] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        events: list[SseEvent] = await log.wait_for_events(cursor, timeout=remaining)
        if not events:
            break  # timed out with no new events
        for e in events:
            cursor = e.id
            if e.event == "agent_message":
                text = e.data.get("text")
                if text:
                    texts.append(str(text))
            elif e.event == "turn_complete":
                return {
                    "replied": True,
                    "reply": "".join(texts) or None,
                    "stop_reason": e.data.get("stop_reason"),
                    "last_id": cursor,
                }
    return {
        "replied": False,
        "reply": "".join(texts) or None,
        "stop_reason": None,
        "last_id": cursor,
    }
