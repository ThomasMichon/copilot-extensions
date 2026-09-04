"""Push-driven supervisor wakes over Agent Bridge's aggregate remote event API."""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

log = logging.getLogger("agent-dispatch.supervisor.events")

REMOTE_EVENT_MULTIPLEX_PROTOCOL_VERSION = 13
_READ_TIMEOUT_SECONDS = 60.0
_RECONNECT_MAX_SECONDS = 30.0
_BOUNDARY_EVENTS = frozenset(
    {
        "assistant.turn_end",
        "handoff_failed",
        "session_handoff",
        "session_state_changed",
    }
)
_CURSOR_RESET_CODES = frozenset({"cursor_invalidated", "replay_gap"})


class BridgeEventError(RuntimeError):
    """The optional local Agent Bridge event-acceleration path is unavailable."""


@dataclass(frozen=True, order=True)
class BridgeSubscription:
    """One exact remote Bridge session observed by a supervisor lane."""

    host: str
    session_id: str
    caller_id: str

    def as_payload(self) -> dict[str, str]:
        return {
            "host": self.host,
            "session_id": self.session_id,
            "caller_id": self.caller_id,
        }


class BridgeEventStream(Protocol):
    def __iter__(self) -> Iterator[dict[str, Any]]: ...

    def close(self) -> None: ...


class BridgeEventClient(Protocol):
    def open_events(
        self, subscriptions: Sequence[BridgeSubscription]
    ) -> BridgeEventStream: ...

    def acknowledge(
        self,
        subscription: BridgeSubscription,
        last_id: int,
        continuity_id: str | None,
    ) -> None: ...


class _SseStream:
    def __init__(self, response: Any) -> None:
        self._response = response

    def __iter__(self) -> Iterator[dict[str, Any]]:
        event = ""
        data: list[str] = []
        for raw_line in self._response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith(":"):
                yield {"event": "_heartbeat", "data": {}}
                continue
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data.append(line[6:])
            elif not line:
                if data:
                    raw = "\n".join(data)
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise BridgeEventError("invalid Agent Bridge SSE payload") from exc
                    yield {"event": event, "data": payload}
                event = ""
                data = []

    def close(self) -> None:
        response, self._response = self._response, None
        if response is not None:
            response.close()


class LocalBridgeEventClient:
    """Independent process-boundary client for the local Agent Bridge daemon."""

    def __init__(self, *, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir

    def _connection(self) -> tuple[str, str]:
        config_dir = self._config_dir or Path(
            os.environ.get("AGENT_BRIDGE_CONFIG_DIR", "~/.agent-bridge")
        ).expanduser()
        explicit = os.environ.get("AGENT_BRIDGE_BASE_URL")
        if explicit:
            base_url = explicit.rstrip("/")
        else:
            try:
                from zdd.routing import read_active_endpoint

                endpoint = read_active_endpoint(config_dir, verify_listener=True)
            except Exception as exc:
                raise BridgeEventError(
                    "Agent Bridge endpoint discovery is unavailable"
                ) from exc
            if endpoint is None:
                raise BridgeEventError("Agent Bridge has no active local endpoint")
            base_url = endpoint.base_url.rstrip("/")
        try:
            auth = yaml.safe_load(
                (config_dir / "auth.yaml").read_text(encoding="utf-8")
            ) or {}
            token = str(auth.get("token") or "")
        except (OSError, ValueError) as exc:
            raise BridgeEventError("Agent Bridge authentication is unavailable") from exc
        if not token:
            raise BridgeEventError("Agent Bridge authentication is unavailable")
        return base_url, token

    @staticmethod
    def _request(
        url: str,
        token: str,
        *,
        body: dict[str, Any] | None = None,
        accept: str = "application/json",
    ) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data)
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Accept", accept)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            return urllib.request.urlopen(request, timeout=_READ_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get(
                    "detail", str(exc)
                )
            except Exception:
                detail = str(exc)
            raise BridgeEventError(f"Agent Bridge rejected event acceleration: {detail}") from exc
        except (OSError, urllib.error.URLError) as exc:
            raise BridgeEventError("Agent Bridge event acceleration is unreachable") from exc

    def _require_protocol(self, base_url: str, token: str) -> None:
        response = self._request(f"{base_url}/health", token)
        try:
            health = json.loads(response.read().decode("utf-8"))
        finally:
            response.close()
        version = int(health.get("protocol_version") or 0)
        minimum = int(health.get("min_protocol_version") or 1)
        required = REMOTE_EVENT_MULTIPLEX_PROTOCOL_VERSION
        if not minimum <= required <= version:
            raise BridgeEventError(
                f"Agent Bridge HTTP protocol {required} is required "
                f"(daemon advertises {minimum}-{version})"
            )

    def open_events(
        self, subscriptions: Sequence[BridgeSubscription]
    ) -> BridgeEventStream:
        base_url, token = self._connection()
        self._require_protocol(base_url, token)
        response = self._request(
            f"{base_url}/api/v1/remote/events",
            token,
            body={
                "subscriptions": [
                    subscription.as_payload() for subscription in subscriptions
                ]
            },
            accept="text/event-stream",
        )
        return _SseStream(response)

    def acknowledge(
        self,
        subscription: BridgeSubscription,
        last_id: int,
        continuity_id: str | None,
    ) -> None:
        base_url, token = self._connection()
        host = urllib.parse.quote(subscription.host, safe="")
        session_id = urllib.parse.quote(subscription.session_id, safe="")
        response = self._request(
            f"{base_url}/api/v1/remote/{host}/sessions/{session_id}/cursor",
            token,
            body={
                "caller_id": subscription.caller_id,
                "last_id": last_id,
                "continuity_id": continuity_id,
            },
        )
        response.close()


class SupervisorEventWake:
    """One coalescing Bridge subscription worker owned by one supervisor."""

    def __init__(
        self,
        client: BridgeEventClient | None = None,
        *,
        reconnect_max: float = _RECONNECT_MAX_SECONDS,
    ) -> None:
        self._client = client or LocalBridgeEventClient()
        self._reconnect_max = max(1.0, reconnect_max)
        self._lock = threading.Lock()
        self._changed = threading.Event()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._desired: tuple[BridgeSubscription, ...] = ()
        self._generation = 0
        self._pending: dict[BridgeSubscription, tuple[int, str | None]] = {}
        self._stream: BridgeEventStream | None = None
        self._thread: threading.Thread | None = None
        self._healthy = False
        self._detail = "idle"
        self._failure_wake_sent = False

    @property
    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "healthy": self._healthy,
                "detail": self._detail,
                "subscriptions": len(self._desired),
            }

    def update(self, subscriptions: Iterable[BridgeSubscription]) -> None:
        desired = tuple(sorted(set(subscriptions)))
        stream: BridgeEventStream | None = None
        with self._lock:
            if desired == self._desired:
                return
            self._desired = desired
            self._generation += 1
            self._pending = {
                key: value for key, value in self._pending.items() if key in desired
            }
            stream = self._stream
            if desired and self._thread is None:
                self._changed.clear()
                self._thread = threading.Thread(
                    target=self._run,
                    name="agent-dispatch-bridge-events",
                    daemon=True,
                )
                self._thread.start()
            else:
                self._changed.set()
            if not desired:
                self._healthy = False
                self._detail = "idle"
                self._failure_wake_sent = False
        if stream is not None:
            stream.close()

    def wait(self, timeout: float) -> bool:
        if timeout < 0:
            raise ValueError("supervisor interval must be non-negative")
        signaled = self._wake.wait(timeout)
        if signaled:
            self._wake.clear()
        return signaled

    def acknowledge(self) -> None:
        with self._lock:
            pending = dict(self._pending)
        for subscription, (last_id, continuity_id) in pending.items():
            try:
                self._client.acknowledge(subscription, last_id, continuity_id)
            except Exception as exc:
                self._degrade(f"cursor acknowledgement failed: {exc}")
                return
            with self._lock:
                if self._pending.get(subscription) == (last_id, continuity_id):
                    self._pending.pop(subscription, None)

    def close(self) -> None:
        self._stop.set()
        self._changed.set()
        with self._lock:
            stream = self._stream
        if stream is not None:
            stream.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _degrade(self, detail: str) -> None:
        wake = False
        with self._lock:
            self._healthy = False
            self._detail = detail
            if not self._failure_wake_sent:
                self._failure_wake_sent = True
                wake = True
        log.warning("Bridge event acceleration degraded: %s", detail)
        if wake:
            self._wake.set()

    def _mark_progress(self) -> None:
        with self._lock:
            self._healthy = True
            self._detail = "connected"
            self._failure_wake_sent = False

    def _queue_cursor_reset(self, data: dict[str, Any]) -> None:
        code = str(data.get("code") or "")
        if code not in _CURSOR_RESET_CODES:
            return
        subscription = BridgeSubscription(
            host=str(data.get("host") or ""),
            session_id=str(data.get("session_id") or ""),
            caller_id=str(data.get("caller_id") or ""),
        )
        try:
            head_id = int(data["head_id"])
        except (KeyError, TypeError, ValueError):
            return
        continuity_id = data.get("current_continuity_id")
        if continuity_id is None:
            continuity_id = data.get("continuity_id")
        with self._lock:
            if subscription not in self._desired:
                return
            self._pending[subscription] = (
                head_id,
                str(continuity_id) if continuity_id is not None else None,
            )

    def _accept(self, event: dict[str, Any]) -> bool:
        event_name = str(event.get("event") or "")
        if event_name == "_heartbeat":
            self._mark_progress()
            return True
        data = event.get("data")
        if not isinstance(data, dict):
            raise BridgeEventError("Agent Bridge event envelope is malformed")
        if event_name == "bridge_control":
            self._queue_cursor_reset(data)
            self._degrade(str(data.get("code") or "Bridge control event"))
            return False
        if event_name != "bridge_event":
            return True
        self._mark_progress()
        name = str(data.get("event") or "")
        if name not in _BOUNDARY_EVENTS:
            return True
        subscription = BridgeSubscription(
            host=str(data.get("host") or ""),
            session_id=str(data.get("session_id") or ""),
            caller_id=str(data.get("caller_id") or ""),
        )
        try:
            event_id = int(data["event_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BridgeEventError("Agent Bridge event cursor is malformed") from exc
        continuity_id = data.get("continuity_id")
        with self._lock:
            if subscription not in self._desired:
                return True
            current = self._pending.get(subscription)
            if current is None or event_id > current[0]:
                self._pending[subscription] = (
                    event_id,
                    str(continuity_id) if continuity_id is not None else None,
                )
        self._wake.set()
        return True

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            with self._lock:
                desired = self._desired
                generation = self._generation
            if not desired:
                self._changed.wait()
                self._changed.clear()
                continue
            stream: BridgeEventStream | None = None
            try:
                stream = self._client.open_events(desired)
                with self._lock:
                    stale = (
                        self._stop.is_set()
                        or generation != self._generation
                        or desired != self._desired
                    )
                    if not stale:
                        self._stream = stream
                        self._detail = "awaiting progress"
                if stale:
                    continue
                backoff = 1.0
                for event in stream:
                    if self._stop.is_set() or self._changed.is_set():
                        break
                    if not self._accept(event):
                        break
                else:
                    if not self._stop.is_set() and not self._changed.is_set():
                        raise BridgeEventError("Agent Bridge event stream exited")
            except Exception as exc:
                if not self._stop.is_set() and not self._changed.is_set():
                    self._degrade(str(exc))
            finally:
                with self._lock:
                    if self._stream is stream:
                        self._stream = None
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
            if self._stop.is_set():
                break
            if self._changed.is_set():
                self._changed.clear()
                backoff = 1.0
                continue
            self._changed.wait(backoff)
            if self._changed.is_set():
                self._changed.clear()
                backoff = 1.0
            else:
                backoff = min(backoff * 2, self._reconnect_max)
