"""Generic telemetry emission seam (pluggable, no-op by default).

A small, dependency-free hook for surfacing session **lifecycle/health**
transitions as structured telemetry, so a downstream consumer can observe the
session state machine **without this plugin depending on any specific telemetry
backend**.

By default emission is a **no-op**: nothing happens until a consumer registers a
sink via :func:`set_telemetry_sink`. A sink is any callable taking one ``dict``
event; :func:`emit` is **fail-open** -- a sink that raises never perturbs the
bridge. This keeps agent-bridge generic: it *declares* its telemetry surface and
ships the hook; the publisher (and its transport) live in whatever consumer
registers a sink.

Vocabulary
----------
Only session **lifecycle/health** events (:data:`LIFECYCLE_EVENTS`) are surfaced
as telemetry -- ``session_state_changed`` / ``connect_failed`` / ``error`` /
``context_warning`` / ``context_critical``. Content-bearing events (user
messages, tool calls, turn text) are deliberately **excluded**: telemetry
carries *state and structure*, never conversation content.
:func:`session_lifecycle_event` shapes one into a generic **state-transition**
record carrying the session id, the event name, and the target status.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import time
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
    event_type: str, session_id: str | None, data: dict[str, Any]
) -> dict[str, Any]:
    """Shape a session lifecycle event into a generic state-transition record.

    Carries only the **session id, the event name, and the target status** --
    never the event's message text, conversation content, or any secret.
    """
    record: dict[str, Any] = {
        "kind": "state_transition",
        "name": "session",
        "event": event_type,
        "session_id": session_id,
    }
    status = data.get("status")
    if status is not None:
        record["to"] = status
    return record
