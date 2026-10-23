"""Observe an existing startup through its event channel without replaying it."""

from __future__ import annotations

import json
import argparse
import math
import queue
import re
import sys
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from agent_procutil.startup import PROGRESS_PREFIX, StartupClock, StartupTimeouts

_active: ContextVar[StartupObservation | None] = ContextVar("startup_observation", default=None)
_label = re.compile(r"[^A-Za-z0-9_.:/ -]")


class StartupObservationError(Exception):
    """Observation ended; this says nothing about remote cancellation or admission."""

    def __init__(self, clock: str | None, phase: str, elapsed: float, *, reason: str = "") -> None:
        self.clock = clock
        self.phase = phase
        self.elapsed = elapsed
        self.reason = reason
        if reason == "admission_unknown":
            boundary = "prompt admission acknowledgement unavailable"
        elif reason == "resume_unknown":
            boundary = "resume readiness acknowledgement unavailable"
        elif clock is None:
            boundary = f"startup observation unavailable at {phase}"
        else:
            boundary = f"startup {clock} timeout after {elapsed:.1f}s at {phase}"
        super().__init__(
            f"{boundary}; target outcome is unconfirmed; "
            "inspect the original operation, do not resend"
        )


def current() -> StartupObservation | None:
    return _active.get()

def positive_seconds(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("startup budget must be finite and positive") from exc
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("startup budget must be finite and positive")
    return value


def validate_options(args: Any) -> None:
    if current() is None:
        return
    if any(getattr(args, name, None) for name in (
        "force", "queue", "native_invocation_file", "plugin_context_stdin",
        "expected_native_session_id",
    )):
        from .send_outcome import SendRefused
        raise SendRefused(
            "refused_invalid", reason="startup_options", retryable=False,
            target=args.target, error="startup observation requires an existing ordinary session",
        )


def configure_client(client: Any, args: Any) -> None:
    observation = current()
    if observation is None:
        return
    from .protocol import STARTUP_PROGRESS_PROTOCOL_VERSION
    if not client.daemon_supports(STARTUP_PROGRESS_PROTOCOL_VERSION):
        from .send_outcome import SendRefused
        raise SendRefused(
            "refused_unavailable", reason="unsupported_protocol", retryable=False,
            target=args.target, error="startup observation requires a progress-capable daemon",
        )
    observation.check()
    observation.local_phase("target-resolution")


def ready() -> None:
    observation = current()
    if observation is not None:
        observation.ready()


def refuse_missing(target: str) -> None:
    if current() is not None:
        from .send_outcome import SendRefused
        raise SendRefused(
            "refused_unavailable", reason="not_found", retryable=False,
            target=target, error="startup observation cannot replace a missing ordinary session",
        )


def refuse_live_target(live: object, address: object, target: str) -> None:
    if current() is not None and (live or address is not None):
        from .send_outcome import SendRefused
        raise SendRefused(
            "refused_invalid", reason="startup_target", retryable=False,
            target=target, error="startup observation requires an existing ordinary hosted session",
        )


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--startup-communication-timeout", type=positive_seconds, metavar="SECONDS",
        help="Observe existing ordinary-session startup with renewable target-contact silence",
    )
    parser.add_argument(
        "--startup-process-timeout", type=positive_seconds, metavar="SECONDS",
        help="Independent startup ceiling, never a turn-execution limit",
    )


def _safe_label(value: object) -> str:
    return _label.sub("_", str(value))[:120]


class StartupObservation:
    def __init__(self, policy: StartupTimeouts) -> None:
        self.clock = StartupClock(policy)
        self.phase = "bridge-connect"
        self.caller_id = "startup-observer:" + uuid.uuid4().hex
        self._output_lock = threading.Lock()

    def report(self, event: str = "progress", **fields: Any) -> None:
        payload = {
            "version": 1, "event": event, "phase": self.phase,
            "elapsed_s": round(self.clock.elapsed, 3), **fields,
        }
        with self._output_lock:
            print(PROGRESS_PREFIX + json.dumps(payload), file=sys.stderr, flush=True)

    def local_phase(self, phase: str) -> None:
        self.phase = _safe_label(phase)
        self.report()

    def received(self, phase: str | None = None, *, event: str = "response") -> None:
        self.check()
        if phase is not None:
            self.phase = _safe_label(phase)
        self.clock.receive(self.phase)
        self.report(target_event=_safe_label(event))

    def check(self) -> None:
        expiry = self.clock.expired()
        if expiry is not None:
            self.report(f"{expiry.clock}_timeout", silence_s=round(expiry.silence, 3))
            raise StartupObservationError(expiry.clock, self.phase, expiry.elapsed)

    def ready(self) -> None:
        if self.clock.ready:
            return
        self.check()
        self.clock.mark_ready()
        self.report("ready", expected_s=self.clock.policy.expected,
                    expected_exceeded=self.clock.elapsed > self.clock.policy.expected)

    def preflight(self, call: Callable[[], Any]) -> Any:
        """Bound synchronous read-only discovery before admitting any mutation."""
        from .client import BridgeClientError

        self.check()
        result: queue.Queue[tuple[Any, BaseException | None]] = queue.Queue()

        def read() -> None:
            try:
                result.put((call(), None))
            except BaseException as exc:
                result.put((None, exc))

        reader = threading.Thread(target=read, name="bridge-startup-preflight", daemon=True)
        reader.start()
        while True:
            self.check()
            try:
                value, error = result.get(timeout=min(self.clock.remaining() or .1, .1))
            except queue.Empty:
                continue
            self.check()
            if error is None or isinstance(error, BridgeClientError):
                self.received(event="bridge-response")
            if error is not None:
                raise error
            return value

    def request(
        self, client: Any, session_id: str, call: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """Subscribe before mutation; losing this observer never cancels the target."""
        from .client import BridgeClientError

        self.local_phase("session-resume")
        messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        stop = threading.Event()

        def events() -> None:
            observing = False
            cursor = None
            continuity = None
            backoff = .1
            try:
                snapshot = client.get_cursor_info(session_id, caller_id=self.caller_id)
                cursor = snapshot.get("head_id")
                continuity = snapshot.get("continuity_id")
                self.received(event="event-cursor")
                if (
                    type(cursor) is not int or cursor < 0
                    or (continuity is not None and (
                        not isinstance(continuity, str) or not continuity
                    ))
                    or cursor > 0 and continuity is None
                ):
                    raise StartupObservationError(
                        None, self.phase, self.clock.elapsed,
                        reason="startup_cursor_unavailable",
                    )
                while not stop.is_set():
                    stream = None
                    try:
                        options = {"continuity_id": continuity} if continuity is not None else {}
                        stream = client.stream_events(
                            session_id, after=cursor, caller_id=self.caller_id,
                            controlled=True, transient=continuity is not None,
                            request_timeout=self.clock.policy.communication, **options,
                        )
                        self.received(event="event-channel-open")
                        if not observing:
                            messages.put(("observing", None))
                            observing = True
                        for event in stream:
                            if stop.is_set():
                                break
                            data = event.get("data")
                            phase = (
                                data.get("phase") if event.get("event") == "startup_progress"
                                and isinstance(data, dict) else None
                            )
                            self.received(phase, event=event.get("event") or "target-update")
                            if event.get("event") in {"cursor_invalidated", "replay_gap"}:
                                messages.put(("observation-error", RuntimeError(
                                    "startup event continuity is unconfirmed",
                                )))
                                return
                            event_id = str(event.get("id") or "")
                            if event_id.isdecimal():
                                cursor = int(event_id)
                            if event.get("continuity_id") is not None:
                                continuity = event["continuity_id"]
                            backoff = .1
                    except Exception as exc:
                        if not stop.is_set():
                            self.report(state="reconnecting", error_type=type(exc).__name__)
                    finally:
                        if stream is not None:
                            stream.close()
                    if not stop.is_set():
                        self.report(state="reconnecting")
                        refresh = getattr(client, "refresh_endpoint", None)
                        if callable(refresh):
                            refresh()
                        stop.wait(backoff)
                        backoff = min(backoff * 2, 2.0)
            except BaseException as exc:
                if not stop.is_set():
                    messages.put(("observation-error", exc))

        def request() -> None:
            try:
                result = call()
                if not stop.is_set():
                    self.received(event="resume-response")
                    if (
                        not isinstance(result, dict)
                        or result.get("status") not in ("idle", "running")
                        or result.get("session_id") != session_id
                    ):
                        raise StartupObservationError(
                            None, self.phase, self.clock.elapsed, reason="resume_unknown",
                        )
                    self.ready()
                    messages.put(("result", result))
            except BaseException as exc:
                if not stop.is_set():
                    if isinstance(exc, BridgeClientError):
                        self.received(event="refused-response")
                    messages.put(("request-error", exc))

        watcher = threading.Thread(target=events, name="bridge-startup-events", daemon=True)
        caller: threading.Thread | None = None
        watcher.start()
        try:
            while True:
                self.check()
                try:
                    kind, value = messages.get(timeout=min(self.clock.remaining() or .1, .1))
                except queue.Empty:
                    continue
                if kind == "observing":
                    self.check()
                    caller = threading.Thread(
                        target=request, name="bridge-startup-request", daemon=True,
                    )
                    caller.start()
                elif kind == "result":
                    self.check()
                    return value
                elif kind == "request-error":
                    if isinstance(value, (BridgeClientError, StartupObservationError)):
                        raise value
                    self.check()
                    self.report("observation_lost", error_type=type(value).__name__)
                    raise StartupObservationError(
                        None, self.phase, self.clock.elapsed,
                        reason="startup_observation_unavailable",
                    ) from value
                elif kind == "observation-error":
                    if isinstance(value, StartupObservationError):
                        raise value
                    self.report("observation_lost", error_type=type(value).__name__)
                    raise StartupObservationError(
                        None, self.phase, self.clock.elapsed,
                        reason="startup_observation_unavailable",
                    ) from value
        finally:
            # A failed observation is not cancellation authority over the request.
            # The reader owns close; cross-thread HTTPResponse.close can block
            # behind its socket read. Its read timeout bounds that thread instead.
            stop.set()
            watcher.join(timeout=.1)
            if caller is not None:
                caller.join(timeout=.1)


@contextmanager
def observing_send(args: Any) -> Iterator[None]:
    communication = getattr(args, "startup_communication_timeout", None)
    process = getattr(args, "startup_process_timeout", None)
    if communication is None and process is None:
        yield
        return
    defaults = StartupTimeouts()
    policy = StartupTimeouts(
        communication=defaults.communication if communication is None else communication,
        process=defaults.process if process is None else process,
    )
    observation = StartupObservation(policy)
    token = _active.set(observation)
    try:
        observation.report()
        yield
    finally:
        _active.reset(token)
