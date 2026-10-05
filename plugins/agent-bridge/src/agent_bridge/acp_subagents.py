"""Sub-agent tracking for the ACP client.

Two independent signals about Copilot sub-agents (its ``task`` tool) live here:

* :data:`BG_TASK_LAUNCH_RE` and friends -- recover background sub-agent
  launch/completion from the ``task`` tool's human-readable output, used by
  ``AcpClient`` to keep the process alive while background work runs.
* :class:`SubagentAttribution` -- attribute recorded session events to the
  sub-agent that produced them, from Copilot's raw session-event feed.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from typing import Any

from acp.exceptions import RequestError

log = logging.getLogger("agent-bridge")

# -- Background-task (sub-agent) detection --------------------------------------
#
# Copilot's `task` tool can launch a sub-agent in *background* mode. The
# orchestrator turn then returns ``end_turn`` while the sub-agent keeps running
# in the same Copilot process (its bash/tool calls stream in after the turn
# settles, and the orchestrator auto-wakes when it completes). Tearing the
# process down in that window kills in-flight background work -- exactly what a
# conversation "waiting on the PR daemon or another agent session" must not
# suffer. There is no structured ACP field for this, so we parse the `task`
# tool's human-readable output (the only authoritative signal Copilot emits):
#
#   launch     -> "Agent started in background with agent_id: <id>. ..."
#   completion -> a later read_agent / task-wait result naming the same
#                 ``agent_id: <id>`` with ``status: completed|failed|...``
#                 (or "Agent is idle (waiting for messages). ... status: idle").
#
# An agent is "active background work" from launch until the first time we
# observe it in a terminal-or-idle status. Idle counts as not-active: an idle
# sub-agent is parked waiting for messages, not making progress, so it does not
# need the connection held open. The match is deliberately tolerant (the phrase
# is product copy that can drift); a missed completion only over-counts, which
# `force` teardown overrides -- it never silently kills live work.
BG_TASK_LAUNCH_RE = re.compile(
    r"started in background with agent_id:\s*([A-Za-z0-9][\w-]*)",
    re.IGNORECASE,
)
BG_TASK_AGENT_ID_RE = re.compile(r"agent_id:\s*([A-Za-z0-9][\w-]*)")
BG_TASK_STATUS_RE = re.compile(r"status:\s*([A-Za-z_]+)")
# Sub-agent statuses that mean "no longer actively running background work".
BG_TASK_INACTIVE_STATUSES = frozenset(
    {
        "completed", "complete", "succeeded", "success",
        "failed", "error", "cancelled", "canceled",
        "idle", "stopped",
    }
)

# -- Sub-agent attribution (raw Copilot session-event feed) ---------------------
#
# Copilot's ACP agent can mirror a requested subset of its internal session
# events as ``github.com/copilot/sessionEvent`` JSON-RPC notifications
# (``params: {sessionId, type, timestamp, agentId?, data}``). The subscription
# is requested through ``_meta["github.com/copilot"].events`` on
# ``initialize`` / ``session/new`` / ``session/load``. Events produced by a
# sub-agent carry ``agentId``; main-agent events do not.
#
# Fail-open: an agent that ignores the ``_meta`` request simply never sends the
# notification and the bridge behaves exactly as before. The list is kept
# short on purpose -- the agent bounds the number/length of requested types and
# may drop mirrored events under backpressure, so nothing here is load-bearing.
# ``tool.execution_complete`` is deliberately NOT requested: it carries the
# full tool result (already recorded via ``tool_call_update``) and nothing the
# attribution needs, so it would only add backpressure.
# Disable with ``AGENT_BRIDGE_SUBAGENT_EVENTS=0``.
COPILOT_META_KEY = "github.com/copilot"
COPILOT_SESSION_EVENT_METHOD = "github.com/copilot/sessionEvent"
SUBAGENT_EVENT_TYPES: tuple[str, ...] = (
    "subagent.started",
    "subagent.completed",
    "subagent.failed",
    "assistant.message",
    "assistant.reasoning",
    "tool.execution_start",
)
# Upper bound on buffered, not-yet-attributed text chunks per stream. A run that
# grows past this is abandoned (never attributed) rather than held unbounded.
MAX_TEXT_RUN_CHUNKS = 20000
# Upper bound on remembered sub-agents / sub-agent tool calls.
MAX_ATTRIBUTION_ENTRIES = 4096
_INVALID_PARAMS = -32602
_LIFECYCLE = {
    "subagent.started": "subagent_started",
    "subagent.completed": "subagent_completed",
    "subagent.failed": "subagent_failed",
}


def subagent_events_enabled() -> bool:
    value = os.environ.get("AGENT_BRIDGE_SUBAGENT_EVENTS", "1").strip().lower()
    return value not in ("0", "false", "no", "off")


def subagent_event_meta() -> dict[str, Any]:
    """``_meta`` kwargs requesting the raw sub-agent event feed (or ``{}``)."""
    if not subagent_events_enabled():
        return {}
    return {COPILOT_META_KEY: {"events": list(SUBAGENT_EVENT_TYPES)}}


def copilot_agent_id_from_meta(meta: Any) -> str | None:
    """Extract ``_meta["github.com/copilot"].agentId`` from an ACP update."""
    if not isinstance(meta, dict):
        return None
    scoped = meta.get(COPILOT_META_KEY)
    if not isinstance(scoped, dict):
        return None
    agent_id = scoped.get("agentId")
    return agent_id if isinstance(agent_id, str) and agent_id else None


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


class SubagentAttribution:
    """Attribute recorded ACP session events to Copilot sub-agents.

    Owned by one ``AcpClient``. ``emit`` records a bridge event;
    ``accepting`` is false while raw events must be ignored (a suppressed
    load replay); ``session_id`` returns the current ACP session id.

    Recorded effects:

    * ``subagent_started`` / ``subagent_completed`` / ``subagent_failed``
      lifecycle events;
    * ``agent_id`` / ``parent_tool_call_id`` on sub-agent tool-call events
      (:meth:`tool_call_fields`);
    * retroactive ``subagent_message`` / ``subagent_thought`` events for
      already-streamed text (:meth:`_close_text_run`).
    """

    def __init__(
        self,
        emit: Callable[[str, dict[str, Any]], None],
        accepting: Callable[[], bool],
        session_id: Callable[[], str | None],
    ) -> None:
        self._emit = emit
        self._accepting = accepting
        self._session_id = session_id
        # agent_id -> launching ``task`` tool_call_id.
        self.subagents: dict[str, str | None] = {}
        # sub-agent tool_call_id -> (agent_id, parent_tool_call_id).
        self.tool_call_agents: dict[str, tuple[str, str | None]] = {}
        # Text of chunks streamed since the last raw boundary (None: overflowed).
        self.runs: dict[str, list[str] | None] = {"message": [], "thought": []}

    # -- Wiring ---------------------------------------------------------------

    def install_route(self, connection: Any) -> None:
        """Route ``github.com/copilot/sessionEvent`` notifications here.

        The ACP SDK only forwards ``_``-prefixed extension notifications to the
        client; any other unknown method is rejected as method-not-found (and
        logged as an error). Register the Copilot raw-event method on the
        connection's router. Best-effort: if the SDK internals differ, the feed
        is simply ignored.
        """
        try:
            from acp.router import Route

            add_route = connection._conn._handler.add_route
        except Exception:
            log.debug("ACP router not reachable; raw session events ignored", exc_info=True)
            return

        async def _on_session_event(params: Any) -> None:
            self.handle_raw_event(params)

        try:
            add_route(Route(
                method=COPILOT_SESSION_EVENT_METHOD,
                func=_on_session_event,
                kind="notification",
            ))
        except Exception:
            log.debug("Could not register raw session-event route", exc_info=True)

    @staticmethod
    async def call_with_meta(method: Callable[..., Any], **kwargs: Any) -> Any:
        """Invoke an ACP RPC requesting the raw event feed, failing open.

        If the agent rejects the request's ``_meta`` as invalid params, retry
        once exactly as the bridge always has (no ``_meta``).
        """
        meta = subagent_event_meta()
        if not meta:
            return await method(**kwargs)
        try:
            return await method(**kwargs, **meta)
        except RequestError as exc:
            if getattr(exc, "code", None) != _INVALID_PARAMS:
                raise
            log.info("Agent rejected the raw session-event request; retrying without it")
            return await method(**kwargs)

    def reset(self) -> None:
        self.subagents.clear()
        self.tool_call_agents.clear()
        self.reset_text_runs()

    def reset_text_runs(self) -> None:
        self.runs = {"message": [], "thought": []}

    # -- session/update hooks ----------------------------------------------------

    def track_text(self, stream: str, text: str) -> None:
        """Buffer a streamed ``message`` / ``thought`` chunk until its boundary."""
        run = self.runs.get(stream)
        if run is None:
            return
        if len(run) >= MAX_TEXT_RUN_CHUNKS:
            self.runs[stream] = None  # never attribute this run
        else:
            run.append(text)

    def tool_call_fields(self, update: Any, *, settled: bool = False) -> dict[str, Any]:
        """``agent_id`` / ``parent_tool_call_id`` for a sub-agent tool call.

        Sources, most direct first: the update's own
        ``_meta["github.com/copilot"].agentId`` (Copilot stamps sub-agent tool
        calls with it), then the raw ``tool.execution_start`` event recorded
        for the same tool_call_id. Main-agent tool calls carry neither and get
        ``{}``, so their event shape is unchanged. ``settled`` forgets the call.
        """
        tool_call_id = getattr(update, "tool_call_id", None)
        meta_agent = copilot_agent_id_from_meta(getattr(update, "field_meta", None))
        known = self.tool_call_agents.get(tool_call_id) if tool_call_id else None
        if known is not None and (meta_agent is None or meta_agent == known[0]):
            agent_id, parent = known
            parent = parent or self.subagents.get(agent_id)
        elif meta_agent is not None:
            agent_id, parent = meta_agent, self.subagents.get(meta_agent)
            if tool_call_id:
                self._remember(self.tool_call_agents, tool_call_id, (agent_id, parent))
        else:
            return {}
        if settled and tool_call_id:
            self.tool_call_agents.pop(tool_call_id, None)
        return {"agent_id": agent_id, "parent_tool_call_id": parent}

    # -- Raw session events ---------------------------------------------------------

    def handle_raw_event(self, params: Any) -> None:
        """Handle one ``github.com/copilot/sessionEvent`` notification."""
        try:
            self._process_raw_event(params)
        except Exception:
            log.debug("Ignoring malformed raw session event", exc_info=True)

    def _process_raw_event(self, params: Any) -> None:
        if not isinstance(params, dict) or not self._accepting():
            return
        current = self._session_id()
        session_id = params.get("sessionId")
        if session_id and current and session_id != current:
            return
        event_type = params.get("type")
        data = params.get("data")
        if not isinstance(data, dict):
            data = {}
        agent_id = _str_or_none(params.get("agentId"))
        parent = _str_or_none(data.get("parentToolCallId"))

        if event_type in _LIFECYCLE:
            if agent_id is None:
                return
            # Lifecycle events name the launching ``task`` call as toolCallId.
            parent = _str_or_none(data.get("toolCallId")) or parent
            parent = parent or self.subagents.get(agent_id)
            payload: dict[str, Any] = {
                "agent_id": agent_id,
                "parent_tool_call_id": parent,
                "agent_name": data.get("agentName"),
                "agent_display_name": data.get("agentDisplayName"),
                "agent_description": data.get("agentDescription"),
                "model": data.get("model"),
            }
            if event_type == "subagent.started":
                self._remember(self.subagents, agent_id, parent)
            elif event_type == "subagent.completed":
                payload["duration_ms"] = data.get("durationMs")
                payload["total_tool_calls"] = data.get("totalToolCalls")
                payload["total_tokens"] = data.get("totalTokens")
            else:
                payload["error"] = data.get("error")
                payload["duration_ms"] = data.get("durationMs")
            self._emit(_LIFECYCLE[event_type], payload)
            return

        if agent_id is not None and parent is None:
            parent = self.subagents.get(agent_id)
        if event_type == "tool.execution_start":
            tool_call_id = _str_or_none(data.get("toolCallId"))
            if agent_id is not None and tool_call_id:
                self._remember(self.tool_call_agents, tool_call_id, (agent_id, parent))
        elif event_type == "assistant.message":
            self._close_text_run("message", data.get("content"), agent_id, parent)
        elif event_type == "assistant.reasoning":
            self._close_text_run("thought", data.get("content"), agent_id, parent)

    def _close_text_run(
        self,
        stream: str,
        content: Any,
        agent_id: str | None,
        parent_tool_call_id: str | None,
    ) -> None:
        """Close a streamed text run at a raw ``assistant.*`` boundary.

        Copilot streams every agent's text -- main agent and sub-agents alike
        -- as untagged ``agent_message_chunk`` / ``agent_thought_chunk``
        updates, then publishes the completed message as one raw
        ``assistant.message`` / ``assistant.reasoning`` event (with
        ``agentId`` only for a sub-agent). The chunks are recorded as they
        arrive (never delayed), so attribution is retroactive and strict:

        * only when the raw content equals, exactly, the concatenation of ALL
          chunks streamed on that stream since the previous boundary, and
        * only when the raw event names a sub-agent,

        emit ``subagent_message`` / ``subagent_thought`` stating that the
        last ``chunk_count`` ``agent_message`` / ``agent_thought`` events
        belong to that sub-agent. Any mismatch (interleaved concurrent
        streams, dropped mirrored events, overflow) yields no attribution, so
        main-agent output is never mislabelled. The text itself is never
        re-recorded.
        """
        run = self.runs.get(stream)
        self.runs[stream] = []
        if not agent_id or not run or not isinstance(content, str) or not content:
            return
        if "".join(run) != content:
            return
        self._emit(f"subagent_{stream}", {
            "agent_id": agent_id,
            "parent_tool_call_id": parent_tool_call_id,
            "chunk_count": len(run),
        })

    @staticmethod
    def _remember(store: dict[str, Any], key: str, value: Any) -> None:
        if key not in store and len(store) >= MAX_ATTRIBUTION_ENTRIES:
            store.pop(next(iter(store)))
        store[key] = value
