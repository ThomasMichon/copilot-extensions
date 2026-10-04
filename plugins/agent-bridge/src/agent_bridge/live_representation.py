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

import re
import time
from bisect import bisect_right
from collections import deque
from threading import Event, Lock
from typing import Any

from .events import EventLog, SseEvent

# Bound on the per-session set of ingested event ids used to dedup redelivery.
# A session's live tail rarely revisits an id older than a few thousand events,
# so a bounded FIFO caps memory without weakening the dedup in practice.
_SEEN_ID_CAP = 4096


def _text(value: Any) -> str | None:
    """Coerce a possibly-missing SDK text field to a non-empty str, or None."""
    if isinstance(value, str) and value:
        return value
    return None


# The body is literal (only attribute values are escaped), so it can itself
# contain "</agent-message>": read through the *final* closing tag.
_ENVELOPE = re.compile(r"<agent-message\b([^>]*)>\s*(.*)\s*</agent-message>", re.S)
_ENVELOPE_ATTR = re.compile(r'([a-z][a-z-]*)="([^"]*)"')
#: Longest relayed message text carried on a represented ``user_message``.
_RELAY_BODY_MAX = 8000


def _unescape_attr(value: str) -> str:
    """Undo the extension's ``escAttr`` (``&``, ``"``, ``<``, ``>``)."""
    return (value.replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&amp;", "&"))


def _relayed(d: dict[str, Any]) -> dict[str, Any]:
    """What a message delivered through this bridge said, and who sent it.

    The live extension delivers an inbox message as an attributed turn: the
    CLI records only the one-line ``Message from <sender> (via agent-bridge)``
    header as ``content`` and the ``<agent-message ...>`` envelope (with the
    text) as ``transformedContent``. Carrying the text as ``relay_body`` (plus
    ``relay_from`` / ``relay_kind``) lets a viewer show the actual message.
    Only bridge deliveries (``source == "agent-bridge"``) are read; anything
    else returns ``{}``.
    """
    if d.get("source") != "agent-bridge":
        return {}
    m = _ENVELOPE.search(str(d.get("transformedContent") or ""))
    if not m:
        return {}
    body = m.group(2).strip()
    out: dict[str, Any] = {"relay_body": body if len(body) <= _RELAY_BODY_MAX else body[:_RELAY_BODY_MAX] + "\u2026"}
    attrs = {k: _unescape_attr(v) for k, v in _ENVELOPE_ATTR.findall(m.group(1))}
    if attrs.get("from"):
        out["relay_from"] = attrs["from"]
    # The extension omits ``kind`` for an ordinary prompt.
    out["relay_kind"] = attrs.get("kind") or "prompt"
    return out


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
        return [("user_message", _out({"content": content, **_relayed(d)}))]

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

    if sdk_type == "session.compaction_start":
        return [(
            "compaction_start",
            _out({
                "conversation_tokens": d.get("conversationTokens"),
                "system_tokens": d.get("systemTokens"),
            }),
        )]

    if sdk_type == "session.compaction_complete":
        return [(
            "compaction_complete",
            _out({
                "success": bool(d.get("success")),
                "tokens_removed": d.get("tokensRemoved"),
                "post_compaction_tokens": d.get("postCompactionTokens"),
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
        data = event.get("data")
        if isinstance(data, dict) and data.get("agentId"):
            continue
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


_MAX_MARKERS = 12
_MARKER_VALUE_MAX = 80


def progress_from_events(
    raw_events: list[dict], prior: dict | None, *, ts: float,
) -> dict[str, object] | None:
    """Fold a represented session's own milestone lines into its progress beat.

    A dispatched worker reports milestones in its replies (``PROGRESS key=value``,
    ``DONE: ...``, ``BLOCKED: ...``) -- the same markers ACP sessions already
    surface (``_parse_progress_markers``). Folding them here gives a live CLI
    session's ``latest_progress`` (the UI's Progress column, ``resolve``) its
    milestones with no extra tool call from the agent. Returns None when the
    batch carries no marker, leaving the prior beat untouched.

    A ``BLOCKED:`` milestone means the session is waiting on someone. Once a
    message is delivered to it (a ``user.message`` that isn't a sub-agent's
    prompt) and it doesn't block again in the same batch, the wait is over: the
    beat keeps its markers but drops the blocker (phase ``resumed``), so a
    session that went back to work never reads as blocked on a stale line.
    """
    from .session_manager import _parse_progress_markers

    markers: dict[str, str] = {}
    done = blocked = None
    answered = False
    for event in raw_events:
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == "user.message" and not data.get("agentId"):
            answered = True
            continue
        if etype != "assistant.message":
            continue
        text = str(data.get("content") or "")
        markers.update(_parse_progress_markers(text))
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("DONE:"):
                done, blocked = line[5:].strip(), None
            elif line.startswith("BLOCKED:"):
                blocked = line[8:].strip()
                answered = False
    if not markers and done is None and blocked is None:
        prior_blocker = (prior or {}).get("blocker")
        if not (answered and prior_blocker):
            return None
        resumed = build_progress_snapshot(
            f"resumed after: {prior_blocker}", phase="resumed",
            pr=(prior or {}).get("pr"), ts=ts,
        )
        if (prior or {}).get("markers"):
            resumed["markers"] = dict(prior["markers"])
        return resumed
    merged = dict((prior or {}).get("markers") or {})
    for key, value in markers.items():
        merged.pop(key, None)  # re-insert so the latest keys come last
        merged[key] = value[:_MARKER_VALUE_MAX]
    merged = dict(list(merged.items())[-_MAX_MARKERS:])
    summary = done or " ".join(f"{k}={v}" for k, v in list(merged.items())[-6:])
    phase = "done" if done is not None else "blocked" if blocked else (
        next(reversed(markers)) if markers else "")
    snapshot = build_progress_snapshot(
        summary, phase=phase, blocker=blocked, pr=merged.get("pr"), ts=ts,
    )
    snapshot["markers"] = merged
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
        # Per-session dedup of already-ingested SDK event ids (the set gives O(1)
        # membership; the deque bounds it FIFO). Guarded by ``_lock``.
        self._seen_ids: dict[str, set[str]] = {}
        self._seen_order: dict[str, deque[str]] = {}
        # SDK event id -> the log event ids it was translated into, so a merge
        # can skip an SDK event both registrations already logged.
        self._sdk_events: dict[str, dict[str, list[int]]] = {}
        self._merged_history: dict[str, tuple[str, dict[int, int]]] = {}
        # Merges still copying events into a surviving log (keyed by its id()):
        # until each is done, that log's merge map is incomplete (``snapshot``).
        self._merging: dict[int, list[Event]] = {}
        self._lock = Lock()

    def get(self, session_id: str) -> EventLog | None:
        """Return the represented log for ``session_id``, or None if none yet."""
        with self._lock:
            return self._logs.get(session_id)

    def snapshot(
        self, session_id: str, *, timeout: float = 5.0
    ) -> tuple[EventLog | None, dict[str, tuple[str, dict[int, int]]]]:
        """``session_id``'s log together with the merge history that matches it.

        ``alias`` serves a merged-away id from the surviving log before it has
        copied that id's events and recorded its id map; a reader that took the
        log then would find no map for a valid reference. So this waits (up to
        ``timeout``; a blocking call, for the sync routes) for merges into that
        log to finish, then reads both under one lock. A merge that starts
        while it waits is waited for too, within the same ``timeout``."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                log = self._logs.get(session_id)
                pending = list(self._merging.get(id(log), ())) if log is not None else []
                if not pending or time.monotonic() >= deadline:
                    return log, dict(self._merged_history)
            for done in pending:
                done.wait(max(0.0, deadline - time.monotonic()))

    def get_or_create(
        self, session_id: str, *, worktree_id: str | None = None
    ) -> EventLog:
        """Return (creating if needed) the represented log for ``session_id``."""
        with self._lock:
            log = self._logs.get(session_id)
            if log is None:
                log = EventLog(
                    session_id=session_id,
                    worktree_id=worktree_id,
                    telemetry_source="represented",
                )  # db=None -> in-memory only
                self._logs[session_id] = log
            elif worktree_id is not None:
                log.set_telemetry_identity(worktree_id=worktree_id)
            return log

    def alias(self, old_id: str, new_id: str) -> None:
        """Serve ``old_id`` and ``new_id`` from one log after a session-id
        change: the predecessor's log (its history, waited sends and readers)
        becomes the successor's, so a reply or stream spanning the rename sees
        every event. If the successor already had a log of its own, its events
        are appended to the predecessor's and its readers are woken to move
        over (a represented stream re-resolves its log on every read)."""
        with self._lock:
            old = self._logs.get(old_id)
            new = self._logs.get(new_id)
            if old is None or old is new:
                return
            # Every key served by the discarded log moves with it (aliasing B->C
            # and then A->C must not leave B on a merged-away log, or a repeated
            # alias would merge the logs back into each other): repeating an
            # alias is then a no-op.
            rebind = [k for k, v in self._logs.items() if new is not None and v is new] or [new_id]
            merged = new
            seen = self._seen_ids.setdefault(old_id, set())
            order = self._seen_order.setdefault(old_id, deque())
            for key in rebind:
                for event_id in self._seen_order.get(key, ()):
                    if event_id not in seen:
                        seen.add(event_id)
                        order.append(event_id)
            old_sdk = self._sdk_events.setdefault(old_id, {})
            sdk_maps = {id(m): m for m in (self._sdk_events.get(k) for k in rebind) if m}
            successor_sdk = {
                eid: (sdk, i)
                for m in sdk_maps.values()
                for sdk, eids in m.items()
                for i, eid in enumerate(eids)
            }
            while len(order) > _SEEN_ID_CAP:
                dropped = order.popleft()
                seen.discard(dropped)
                old_sdk.pop(dropped, None)
            for key in rebind:
                self._logs[key] = old
                self._seen_ids[key] = seen
                self._seen_order[key] = order
                self._sdk_events[key] = old_sdk
            done = Event()
            if merged is not None:
                self._merging.setdefault(id(old), []).append(done)
        if merged is None:
            return
        try:
            self._copy_merged(old, merged, old_sdk, successor_sdk)
        finally:
            with self._lock:
                waiting = self._merging.get(id(old), [])
                if done in waiting:
                    waiting.remove(done)
                if not waiting:
                    self._merging.pop(id(old), None)
            done.set()

    def _copy_merged(
        self, old: EventLog, merged: EventLog, old_sdk: dict[str, list[int]],
        successor_sdk: dict[int, tuple[str, int]],
    ) -> None:
        """Append ``merged``'s events to ``old`` and record the id map; ``alias``
        has already pointed every key at ``old``."""
        prior = merged.continuity_id
        # Cursor 0 on the successor means "after everything it had", which is
        # the predecessor's tail at merge time -- not the predecessor's start.
        ids = {0: old.latest_id}
        for evt in merged.get_events(0):
            sdk, i = successor_sdk.get(evt.id, (None, 0))
            with self._lock:
                retained = list(old_sdk.get(sdk) or ()) if sdk else []
            if retained:
                # Both registrations logged this SDK event before the rename:
                # keep the predecessor's copy. ``ids`` stays exact (a detail
                # reference names the very event); cursors read it through
                # ``merged_cursor``, which never moves back.
                ids[evt.id] = retained[min(i, len(retained) - 1)]
                continue
            appended = ids[evt.id] = old.append(evt.event, evt.data, timestamp=evt.timestamp).id
            if sdk:
                with self._lock:
                    old_sdk.setdefault(sdk, []).append(appended)
        ids = MergedIds(ids)
        merged.merged_into = (old, ids)
        if prior and old.continuity_id:
            with self._lock:
                self._merged_history[prior] = (old.continuity_id, ids)
        merged.wake_waiters()

    def merged_history(self) -> dict[str, tuple[str, dict[int, int]]]:
        """Continuity of each log merged away -> (merged continuity, id map), so
        result tokens minted on it can be retargeted (``result_tokens.retarget``)."""
        with self._lock:
            return dict(self._merged_history)

    def ids_of(self, session_id: str) -> list[str]:
        """Every key sharing ``session_id``'s log (itself and its retired ids)."""
        with self._lock:
            log = self._logs.get(session_id)
            return [k for k, v in self._logs.items() if log is not None and v is log]

    def drop(self, session_id: str) -> None:
        """Forget a session's represented log (on deregister) to free memory;
        once no alias still serves that log, its merge mappings go too."""
        with self._lock:
            log = self._logs.pop(session_id, None)
            self._seen_ids.pop(session_id, None)
            self._seen_order.pop(session_id, None)
            self._sdk_events.pop(session_id, None)
            if log is not None and not any(v is log for v in self._logs.values()):
                # The discarded continuity and every log merged into it,
                # transitively (C -> B -> A): none can be retargeted any more.
                gone = {log.continuity_id}
                while extra := {k for k, (c, _) in self._merged_history.items()
                                if c in gone and k not in gone}:
                    gone |= extra
                for key in [k for k, (c, _) in self._merged_history.items() if k in gone or c in gone]:
                    del self._merged_history[key]

    def _mark_seen(self, session_id: str, event_id: str) -> bool:
        """Record ``event_id`` for ``session_id``; return True if it is new.

        Returns False when this id was already ingested (a redelivery). Bounded
        FIFO per session; thread-safe.
        """
        with self._lock:
            seen = self._seen_ids.get(session_id)
            if seen is None:
                seen = set()
                self._seen_ids[session_id] = seen
                self._seen_order[session_id] = deque()
            if event_id in seen:
                return False
            seen.add(event_id)
            order = self._seen_order[session_id]
            order.append(event_id)
            if len(order) > _SEEN_ID_CAP:
                dropped = order.popleft()
                seen.discard(dropped)
                self._sdk_events.get(session_id, {}).pop(dropped, None)
            return True

    def _record_sdk_event(self, session_id: str, sdk_id: str, log_event_id: int) -> None:
        with self._lock:
            if sdk_id in self._seen_ids.get(session_id, ()):
                self._sdk_events.setdefault(session_id, {}).setdefault(sdk_id, []).append(log_event_id)

    def ingest(
        self,
        session_id: str,
        sdk_events: list[dict[str, Any]],
        *,
        worktree_id: str | None = None,
    ) -> int:
        """Translate + append a batch of raw SDK events; return the count appended.

        Each item is a raw SDK event ``{"type": str, "data": dict, "id": str}``.
        Events that translate to nothing (unknown/dropped types) are silently
        skipped.

        Defense-in-depth dedup: the extension already dedups the CLI runtime's
        per-subscription redelivery (one logical event arrives at its handler
        once per live-session subscription, all sharing the same ``id``), but a
        bridge restart, a retried POST, or a second producer could still resend
        an event already logged. Any ``id`` already ingested for this session is
        skipped; an event with no ``id`` cannot be deduped and is appended as
        before (honest best-effort).
        """
        log = self.get_or_create(session_id, worktree_id=worktree_id)
        appended = 0
        for item in sdk_events:
            if not isinstance(item, dict):
                continue
            sdk_type = item.get("type")
            if not isinstance(sdk_type, str):
                continue
            event_id = item.get("id")
            if (
                isinstance(event_id, str)
                and event_id
                and not self._mark_seen(session_id, event_id)
            ):
                continue
            data = item.get("data")
            data = data if isinstance(data, dict) else {}
            for event_type, payload in translate_sdk_event(sdk_type, data):
                appended_id = log.append(event_type, payload).id
                if isinstance(event_id, str) and event_id:
                    self._record_sdk_event(session_id, event_id, appended_id)
                appended += 1
        return appended


# -- D1: read a live session's reply turn from its represented stream --------

# One turn's worth of collected assistant text plus how it ended.
TurnReply = dict[str, Any]


class MergedIds(dict):
    """A merged-away log's id map (its id -> the merged log's id), complete when
    published and never changed after, with a prefix-max index over its sorted
    ids so :func:`merged_cursor` bisects instead of scanning the whole map on
    every reconnect or merge-follow read."""

    def __init__(self, ids: dict[int, int]) -> None:
        super().__init__(ids)
        self.keys_sorted = sorted(self)
        self.prefix_max: list[int] = []
        for k in self.keys_sorted:
            prior = self.prefix_max[-1] if self.prefix_max else self[k]
            self.prefix_max.append(max(self[k], prior))


def merged_cursor(ids: dict[int, int], cursor: int) -> int:
    """A read cursor in the merged numbering: the furthest merged id at or
    before it, so a reader never moves back into history it already read
    (``ids`` is exact; a skipped duplicate maps to its earlier retained copy)."""
    index = ids if isinstance(ids, MergedIds) else MergedIds(ids)
    i = bisect_right(index.keys_sorted, cursor)
    return index.prefix_max[i - 1] if i else cursor


def translate_merged_cursor(prev: EventLog, current: EventLog, cursor: int) -> int | None:
    """If ``prev`` was merged into ``current`` -- directly or through several
    merges (C->B->A) -- return ``cursor`` in the merged numbering, translated at
    each step (see :func:`merged_cursor`); else None. A merge cycle stops it."""
    log, seen = prev, set()
    while log is not current:
        follow = getattr(log, "merged_into", None)
        if follow is None or id(log) in seen:
            return None
        seen.add(id(log))
        log, cursor = follow[0], merged_cursor(follow[1], cursor)
    return None if log is prev else cursor


class MergeFollowingLog:
    """A read view of a represented session's log for a long-lived reader (an
    SSE stream) that keeps its own cursor: when a session-id change merges the
    log into another, the reader's cursor is translated to the merged numbering
    instead of re-reading the other log's history from that raw id."""

    def __init__(self, store: LiveEventStore, session_id: str, log: EventLog) -> None:
        self._store, self._session_id, self._log = store, session_id, log
        self._moved: tuple[int, int] | None = None  # (old cursor, translated)

    def _follow(self, cursor: int) -> tuple[EventLog, int]:
        original = cursor
        pending = self._moved is not None and cursor == self._moved[0]
        if pending:
            cursor = self._moved[1]  # not advanced since the last merge: already in self._log's numbering
        current = self._store.get(self._session_id) or self._log
        if current is not self._log:
            # Translate from the numbering the cursor is actually in, so two merges
            # with only a heartbeat poll between them (C:2 -> B:3 -> A:4) chain.
            moved = translate_merged_cursor(self._log, current, cursor)
            self._log = current
            self._moved = (original, moved) if moved is not None else None
            if moved is not None:
                cursor = moved
        return current, cursor

    async def wait_for_events_snapshot(self, cursor: int, *, timeout: float):
        log, cursor = self._follow(cursor)
        return await log.wait_for_events_snapshot(cursor, timeout=timeout)

    async def wait_for_events(self, cursor: int, *, timeout: float):
        log, cursor = self._follow(cursor)
        return await log.wait_for_events(cursor, timeout=timeout)

    def __getattr__(self, name: str):
        return getattr(self._store.get(self._session_id) or self._log, name)

    @property
    def followed_continuity_id(self) -> str | None:
        """Continuity of the log this reader's cursor is currently numbered on
        (it switches only when the reader's next wait follows a merge)."""
        return self._log.continuity_id

    @property
    def translated_cursor(self) -> int | None:
        """The reader's cursor in the merged numbering, right after following a merge."""
        return self._moved[1] if self._moved is not None else None


def translate_reconnect_cursor(
    store: LiveEventStore, log: EventLog, continuity_id: str | None, after: int,
) -> int:
    """``after`` numbered on the log named ``continuity_id``, in ``log``'s numbering
    when that log was since merged (possibly in steps) into ``log``. A cursor named
    for a log this one never absorbed (e.g. one lost in a daemon restart, when the
    merge map is empty) can't be translated, so the reader replays from 0 rather
    than skip the new log's events up to the old cursor; no continuity: unchanged."""
    if not continuity_id or continuity_id == log.continuity_id:
        return after
    merged = store.merged_history()
    for _ in range(len(merged)):
        if continuity_id not in merged:
            break
        continuity_id, ids = merged[continuity_id]
        after = merged_cursor(ids, after)
        if continuity_id == log.continuity_id:
            return after
    return 0


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
        # A session-id change merged this log into another, maybe more than
        # once (C -> B -> A) while we slept: follow every completed merge, with
        # the cursor translated to each merged numbering, before waiting again.
        while log.merged_into is not None:
            merged_log = log.merged_into[0]
            cursor = translate_merged_cursor(log, merged_log, cursor)
            log = merged_log
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        events: list[SseEvent] = await log.wait_for_events(cursor, timeout=remaining)
        if log.merged_into is not None:
            continue
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
