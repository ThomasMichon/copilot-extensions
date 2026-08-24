"""Generic telemetry emission seam (pluggable, no-op by default).

A small, dependency-free hook for surfacing session, conversation, and tool-call
**lifecycle/health** transitions as structured telemetry, so a downstream
consumer can replay the state machine **without this plugin depending on any
specific telemetry backend**.

By default emission is a **no-op**: nothing happens until a consumer registers a
sink via :func:`set_telemetry_sink`. A sink is any callable taking one ``dict``
event; :func:`emit` is **fail-open** -- a sink that raises never perturbs the
bridge. This keeps agent-bridge generic: it *declares* its telemetry surface and
ships the hook; the publisher (and its transport) live in whatever consumer
registers a sink.

Vocabulary
----------
The reducer observes only structural event types. It emits replayable,
content-free transitions for:

* session status;
* conversation turns (``idle -> sending -> responding -> end-turn -> idle``);
* cancellation and error terminals; and
* tool-call start and terminal outcomes.

Prompt text, response text, tool input/output, error messages, and tool titles
are excluded by construction. The reducer carries only stable identities,
event cursors, status/stop reason, and tool kind/id.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("agent-bridge.telemetry")

#: A telemetry sink: a callable receiving one structured event dict.
TelemetrySink = Callable[[dict[str, Any]], None]

#: Env var naming a sink **factory** to install at startup (see
#: :func:`load_sink_from_env`). Value form: ``"package.module:make_sink"``.
SINK_ENV_VAR = "AGENT_BRIDGE_TELEMETRY_SINK"

#: Config file consulted at startup for a sink spec, discovered by **convention**
#: -- no environment variable points at it (see :func:`load_sink_from_config`). A
#: JSON object with a top-level ``"sink": "package.module:factory"`` key. This is
#: the **env-free** wiring path: dropping this file attaches a sink without
#: setting :data:`SINK_ENV_VAR`.
CONFIG_FILENAME = "telemetry.json"

#: ``module:factory`` spec of the **built-in spool sink** shipped by this plugin
#: (see :func:`make_spool_sink`). A consumer selects it from the config file with
#: ``{"sink": "agent_bridge.telemetry:make_spool_sink", "spool": "<path>"}`` -- no
#: external package, so the daemon's interpreter stays free of any consumer
#: dependency (the sink runs entirely inside this plugin's own environment). The
#: out-of-process consumer drains the spool file on its own schedule.
SPOOL_SINK_SPEC = "agent_bridge.telemetry:make_spool_sink"


def _default_config_path() -> Path:
    """Convention location of the telemetry config file.

    ``<config dir>/telemetry.json`` -- the bridge's config/state directory
    (``~/.agent-bridge`` by default, honoring ``AGENT_BRIDGE_CONFIG_DIR``), the
    same root that holds ``config.yaml``.
    """
    base = Path(os.environ.get("AGENT_BRIDGE_CONFIG_DIR", "~/.agent-bridge")).expanduser()
    return base / CONFIG_FILENAME


#: Event types that represent a session lifecycle/health transition worth
#: surfacing as telemetry. Content-bearing events (``user_message``, tool-call
#: events, turn text) are excluded -- telemetry carries state, not content.
LIFECYCLE_EVENTS = frozenset(
    {
        "session_state_changed",
        "connect_failed",
        "error",
        "context_warning",
        "context_critical",
    }
)

_STRUCTURAL_EVENTS = LIFECYCLE_EVENTS | frozenset(
    {
        "user_message",
        "agent_message",
        "agent_thought",
        "turn_complete",
        "tool_call",
        "tool_call_start",
        "tool_call_update",
        "tool_call_progress",
    }
)

_TOOL_TERMINALS = {
    "completed": "completed",
    "complete": "completed",
    "success": "completed",
    "succeeded": "completed",
    "failed": "error",
    "error": "error",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}

_CONVERSATION_SENDABLE_STATES = {
    None,
    "idle",
    "end-turn",
    "cancelled",
    "error",
    "stopped",
    "ended",
}

_MAX_TRACKED_TOOLS = 1024

_STOP_REASONS = frozenset(
    {
        "end_turn",
        "max_tokens",
        "max_turn_requests",
        "refusal",
        "cancelled",
        "canceled",
        "interrupted",
    }
)

_SAFE_HEALTH_FIELDS = frozenset(
    {"context_pct", "threshold", "stage_name", "retryable"}
)

_TRIGGERS = frozenset(
    {"daemon_restart", "handoff", "interrupt", "resume", "resync"}
)

_sink: TelemetrySink | None = None


def set_telemetry_sink(sink: TelemetrySink | None) -> None:
    """Register (or clear, with ``None``) the process-wide telemetry sink."""
    global _sink
    _sink = sink


def clear_telemetry_sink() -> None:
    """Remove any registered sink -- emission returns to a no-op."""
    set_telemetry_sink(None)


def has_sink() -> bool:
    """True when a sink is registered (emission will be delivered)."""
    return _sink is not None


def emit(event: dict[str, Any]) -> None:
    """Emit one telemetry event to the registered sink (fail-open).

    No sink registered -> a no-op. A sink that raises is swallowed (logged at
    debug): telemetry is best-effort and must never perturb the bridge.
    """
    sink = _sink
    if sink is None:
        return
    try:
        sink(event)
    except Exception:  # noqa: BLE001 - telemetry is best-effort, never fatal
        log.debug("telemetry sink raised; dropping event", exc_info=True)


def load_sink_from_spec(spec: str) -> TelemetrySink | None:
    """Import and build a sink from a ``"package.module:factory"`` spec.

    The named attribute is a **factory**: a zero-arg callable returning the
    actual sink (a ``Callable[[dict], None]``). Factory semantics let a real sink
    open its own resources and read its own configuration at install time.
    Returns the built sink, or ``None`` on any failure (fail-open: a bad spec
    never raises to the caller).
    """
    spec = (spec or "").strip()
    if not spec:
        return None
    if ":" not in spec:
        log.warning("telemetry sink spec %r is not 'module:factory'; ignoring", spec)
        return None
    module_path, _, attr = spec.partition(":")
    try:
        module = importlib.import_module(module_path)
        factory = getattr(module, attr)
        sink = factory()
    except Exception:  # noqa: BLE001 - a bad sink spec must never break startup
        log.warning("could not load telemetry sink from %r; telemetry stays off",
                    spec, exc_info=True)
        return None
    if not callable(sink):
        log.warning("telemetry sink factory %r did not return a callable; ignoring",
                    spec)
        return None
    return sink


def load_sink_from_env(var: str = SINK_ENV_VAR) -> bool:
    """Install a telemetry sink named by the environment, if any.

    Reads ``var`` (default :data:`SINK_ENV_VAR`); when it holds a
    ``"module:factory"`` spec, builds the sink and registers it. Returns ``True``
    when a sink was installed. Fail-open: an unset var or a bad spec leaves
    emission a no-op. This is the seam a consumer uses to attach a publisher to
    a bridge process it does not own -- config, not code.
    """
    sink = load_sink_from_spec(os.environ.get(var, ""))
    if sink is None:
        return False
    set_telemetry_sink(sink)
    log.info("telemetry sink installed from %s", var)
    return True


def load_sink_from_config(path: str | os.PathLike[str] | None = None) -> bool:
    """Install a telemetry sink named by a **convention-located config file**, if any.

    Reads a JSON file (default :func:`_default_config_path`, i.e.
    ``~/.agent-bridge/telemetry.json``) whose top-level ``"sink"`` key holds a
    ``"module:factory"`` spec, builds the sink, and registers it. Returns ``True``
    when a sink was installed. Fail-open: a missing/unreadable file, invalid JSON,
    or a bad spec leaves emission a no-op. This is the **env-free** wiring path --
    a host attaches a sink by dropping this file, with **no** environment variable.
    """
    p = Path(path) if path is not None else _default_config_path()
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return False  # no config file (or unreadable) -> a silent no-op
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("telemetry config %s is not valid JSON; telemetry stays off", p)
        return False
    spec = str(data.get("sink") or "").strip() if isinstance(data, dict) else ""
    sink = load_sink_from_spec(spec)
    if sink is None:
        return False
    set_telemetry_sink(sink)
    log.info("telemetry sink installed from %s", p)
    return True


def _configured_spool_path(path: str | os.PathLike[str] | None = None) -> str | None:
    """The ``"spool"`` path declared in the telemetry config file, or ``None``.

    Read from the same convention-located config file the sink spec comes from
    (:func:`_default_config_path`). Lets the built-in :func:`make_spool_sink`
    discover its output path from the declaration, keeping the zero-arg factory
    contract of :func:`load_sink_from_spec`.
    """
    p = Path(path) if path is not None else _default_config_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    spool = data.get("spool")
    return spool.strip() if isinstance(spool, str) and spool.strip() else None


def make_spool_sink(spool: str | os.PathLike[str] | None = None) -> TelemetrySink | None:
    """Built-in, dependency-free sink that appends each event to a **spool file**.

    A batteries-included telemetry backend that keeps the daemon's process
    **self-contained**: instead of importing a consumer's package, the consumer
    selects this sink by declaration (:data:`SPOOL_SINK_SPEC`) and names a
    ``"spool"`` path in the config file, then drains that file **out of process**
    on its own schedule. Each event is written as one JSON-Lines record (the
    generic event dict, stamped with an emit ``ts`` in epoch milliseconds if it
    lacks one) -- the transport is a plain append-only file, nothing more.

    Zero-arg-callable so :func:`load_sink_from_spec` can invoke it as a factory;
    the spool path falls back to the config file's ``"spool"`` key. Returns
    ``None`` (a no-op, fail-open) when no spool path is configured.
    """
    target = spool if spool is not None else _configured_spool_path()
    target = str(target).strip() if target else ""
    if not target:
        log.warning("spool sink: no 'spool' path configured; telemetry stays off")
        return None
    spool_path = Path(target).expanduser()

    def sink(event: dict[str, Any]) -> None:
        try:
            rec = dict(event)
            rec.setdefault("ts", int(time.time() * 1000))
            line = json.dumps(rec, separators=(",", ":"), default=str)
            with open(spool_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:  # noqa: BLE001 - a sink is best-effort, never fatal
            log.debug("spool sink write failed; dropping event", exc_info=True)

    return sink


def session_lifecycle_event(
    event_type: str,
    session_id: str | None,
    data: dict[str, Any],
    *,
    from_state: str | None = None,
    identity: dict[str, Any] | None = None,
    event_id: int | None = None,
) -> dict[str, Any]:
    """Shape a session lifecycle event into a generic state-transition record.

    Carries only the **session id, the event name, and the target status** --
    never the event's message text, conversation content, or any secret.
    """
    record: dict[str, Any] = {
        "kind": (
            "state_transition"
            if event_type == "session_state_changed"
            else "error" if event_type in {"connect_failed", "error"} else "event"
        ),
        "name": (
            "session"
            if event_type == "session_state_changed"
            else "session_health"
        ),
        "event": event_type,
        "session_id": session_id,
    }
    if identity:
        record.update({k: v for k, v in identity.items() if v is not None})
    if event_id is not None:
        record["event_id"] = event_id
    if event_type == "session_state_changed" and from_state is not None:
        record["from"] = from_state
    status = data.get("status")
    if status is not None:
        record["to"] = status
    trigger = _bounded_trigger(data.get("trigger"))
    if trigger is not None:
        record["trigger"] = trigger
    if event_type != "session_state_changed":
        record.update(
            {
                key: data[key]
                for key in _SAFE_HEALTH_FIELDS
                if key in data
            }
        )
    return record


class SessionTraceReducer:
    """Reduce durable session events into content-free telemetry transitions.

    One reducer belongs to one session event log. It tracks current session,
    conversation, and per-tool state so every emitted transition carries a
    meaningful ``from``. Replaying a persisted log through :meth:`observe`
    rebuilds that reducer state without requiring any telemetry emission.
    """

    def __init__(
        self,
        session_id: str | None,
        *,
        acp_session_id: str | None = None,
        worktree_id: str | None = None,
        source: str = "owned",
    ) -> None:
        self.session_id = session_id
        self._identity: dict[str, Any] = {
            "acp_session_id": acp_session_id,
            "worktree_id": worktree_id,
            "source": source,
        }
        self.log_epoch = uuid.uuid4().hex
        self._origin_set = False
        self.reset()

    def reset(self) -> None:
        """Reset derived state while retaining the current log epoch."""
        self.session_state: str | None = None
        self.conversation_state: str | None = None
        self.tool_states: dict[str, str] = {}

    def set_log_origin(self, timestamp: float) -> None:
        """Derive a stable epoch from the durable log's first event."""
        if self._origin_set:
            return
        origin = f"{self.session_id or ''}:{timestamp!r}".encode()
        self.log_epoch = hashlib.sha256(origin).hexdigest()[:16]
        self._origin_set = True

    def begin_rebuild(self) -> str:
        """Rotate reducer state before replaying an authoritative rebuilt log."""
        prior_epoch = self.log_epoch
        self.log_epoch = uuid.uuid4().hex
        self._origin_set = False
        self.reset()
        return prior_epoch

    def complete_rebuild(
        self, prior_epoch: str, event_count: int
    ) -> dict[str, Any]:
        """Describe the rebuilt log and its post-replay state."""
        marker: dict[str, Any] = {
            "kind": "event",
            "name": "event_log",
            "event": "log_rebuilt",
            "session_id": self.session_id,
            "from": prior_epoch,
            "event_count": event_count,
            "session_state": self.session_state,
            "conversation_state": self.conversation_state,
            "active_tool_call_ids": sorted(
                tool_id
                for tool_id, state in self.tool_states.items()
                if state == "running"
            ),
        }
        if event_count:
            marker["to"] = self.log_epoch
            marker["log_epoch"] = self.log_epoch
        else:
            # No first event exists from which to derive a restart-stable epoch.
            # The next event_id=1 establishes it; until then the log is empty.
            self.log_epoch = ""
            self._origin_set = False
        marker.update({k: v for k, v in self._identity.items() if v is not None})
        return marker

    def update_identity(
        self,
        *,
        acp_session_id: str | None = None,
        worktree_id: str | None = None,
    ) -> None:
        """Fill identities learned after the event log was constructed."""
        if acp_session_id is not None:
            self._identity["acp_session_id"] = acp_session_id
        if worktree_id is not None:
            self._identity["worktree_id"] = worktree_id

    def observe(
        self,
        event_type: str,
        data: dict[str, Any],
        *,
        event_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Apply one durable event and return zero or more transitions."""
        if event_type not in _STRUCTURAL_EVENTS:
            return []

        records: list[dict[str, Any]] = []

        if event_type in LIFECYCLE_EVENTS:
            if event_type == "session_state_changed":
                status = _status(data.get("status"))
                if status is not None and status != self.session_state:
                    records.append(
                        self._transition(
                            "session",
                            self.session_state,
                            status,
                            event_type,
                            event_id=event_id,
                            trigger=data.get("trigger"),
                        )
                    )
                    self.session_state = status
            else:
                records.append(
                    session_lifecycle_event(
                        event_type,
                        self.session_id,
                        data,
                        from_state=self.session_state,
                        identity={**self._identity, "log_epoch": self.log_epoch},
                        event_id=event_id,
                    )
                )

        if (
            event_type == "user_message"
            and self.conversation_state in _CONVERSATION_SENDABLE_STATES
        ):
            self._conversation_transition(
                records, "sending", event_type, event_id=event_id
            )
        elif event_type == "session_state_changed":
            status = _status(data.get("status"))
            target = {
                "running": "responding",
                "idle": "idle",
                "stopped": "stopped",
                "ended": "ended",
                "failed": "error",
            }.get(status or "")
            if target is not None:
                self._conversation_transition(
                    records,
                    target,
                    event_type,
                    event_id=event_id,
                    extra={"turn_index": data.get("turn_index")},
                    trigger=data.get("trigger"),
                )
                tool_terminal = {
                    "idle": "completed",
                    "stopped": "cancelled",
                    "ended": "cancelled",
                    "error": "error",
                }.get(target)
                if tool_terminal is not None:
                    self._terminalize_tools(
                        records,
                        tool_terminal,
                        event_type,
                        event_id=event_id,
                        trigger=data.get("trigger"),
                    )
        elif (
            event_type in {"agent_message", "agent_thought"}
            and self.conversation_state != "responding"
        ):
            self._conversation_transition(
                records, "responding", event_type, event_id=event_id
            )
        elif event_type == "turn_complete":
            stop_reason = _normalized_stop_reason(data.get("stop_reason"))
            target = (
                "cancelled"
                if stop_reason in {"cancelled", "canceled", "interrupted"}
                else "end-turn"
            )
            self._conversation_transition(
                records,
                target,
                event_type,
                event_id=event_id,
                extra={"stop_reason": stop_reason},
            )
            self._terminalize_tools(
                records,
                "cancelled" if target == "cancelled" else "completed",
                event_type,
                event_id=event_id,
            )
            if self._identity.get("source") == "represented":
                self._conversation_transition(
                    records, "idle", event_type, event_id=event_id
                )
        elif event_type == "error":
            self._conversation_transition(
                records, "error", event_type, event_id=event_id
            )
            self._terminalize_tools(
                records, "error", event_type, event_id=event_id
            )

        if event_type in {"tool_call", "tool_call_start"}:
            tool_id = _identifier(data.get("tool_call_id"))
            if tool_id:
                current = self.tool_states.get(tool_id)
                if current != "running":
                    records.append(
                        self._transition(
                            "tool_call",
                            current,
                            "running",
                            event_type,
                            event_id=event_id,
                            extra={
                                "tool_call_id": tool_id,
                                "tool_kind": (
                                    data.get("kind")
                                    if self._identity.get("source") != "represented"
                                    else None
                                ),
                            },
                        )
                    )
                    self.tool_states[tool_id] = "running"
                    self._bound_tool_states()
        elif event_type in {"tool_call_update", "tool_call_progress"}:
            tool_id = _identifier(data.get("tool_call_id"))
            terminal = _TOOL_TERMINALS.get(_status(data.get("status")) or "")
            if tool_id and terminal:
                current = self.tool_states.get(tool_id)
                if current != terminal:
                    records.append(
                        self._transition(
                            "tool_call",
                            current,
                            terminal,
                            event_type,
                            event_id=event_id,
                            extra={
                                "tool_call_id": tool_id,
                                "tool_status": terminal,
                            },
                        )
                    )
                self.tool_states[tool_id] = terminal
                self._bound_tool_states()

        return records

    def _conversation_transition(
        self,
        records: list[dict[str, Any]],
        target: str,
        event_type: str,
        *,
        event_id: int | None,
        extra: dict[str, Any] | None = None,
        trigger: Any = None,
    ) -> None:
        current = self.conversation_state
        if current == target:
            return
        records.append(
            self._transition(
                "conversation",
                current,
                target,
                event_type,
                event_id=event_id,
                extra=extra,
                trigger=trigger,
            )
        )
        self.conversation_state = target

    def _transition(
        self,
        name: str,
        from_state: str | None,
        to_state: str,
        event_type: str,
        *,
        event_id: int | None,
        extra: dict[str, Any] | None = None,
        trigger: Any = None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "kind": "state_transition",
            "name": name,
            "event": event_type,
            "session_id": self.session_id,
            "to": to_state,
            "log_epoch": self.log_epoch,
        }
        if from_state is not None:
            record["from"] = from_state
        if event_id is not None:
            record["event_id"] = event_id
        bounded_trigger = _bounded_trigger(trigger)
        if bounded_trigger is not None:
            record["trigger"] = bounded_trigger
        record.update({k: v for k, v in self._identity.items() if v is not None})
        if extra:
            record.update({k: v for k, v in extra.items() if v is not None})
        return record

    def _terminalize_tools(
        self,
        records: list[dict[str, Any]],
        target: str,
        event_type: str,
        *,
        event_id: int | None,
        trigger: Any = None,
    ) -> None:
        for tool_id, state in list(self.tool_states.items()):
            if state != "running":
                continue
            records.append(
                self._transition(
                    "tool_call",
                    state,
                    target,
                    event_type,
                    event_id=event_id,
                    extra={
                        "tool_call_id": tool_id,
                        "tool_status": target,
                    },
                    trigger=trigger,
                )
            )
            self.tool_states[tool_id] = target

    def _bound_tool_states(self) -> None:
        while len(self.tool_states) > _MAX_TRACKED_TOOLS:
            self.tool_states.pop(next(iter(self.tool_states)))


def _status(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _identifier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized_stop_reason(value: Any) -> str | None:
    status = _status(value)
    if status is None:
        return None
    return status if status in _STOP_REASONS else "other"


def _bounded_trigger(value: Any) -> str | None:
    trigger = _status(value)
    if trigger is None:
        return None
    return trigger if trigger in _TRIGGERS else "other"
