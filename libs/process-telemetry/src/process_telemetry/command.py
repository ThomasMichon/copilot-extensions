"""Bound command routes on the existing producer-owned buffer, not an event bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import count
import json
import subprocess
import threading
import time
import uuid
from typing import Any, TYPE_CHECKING

from .buffer import BufferedSink, _Counter
from .command_config import Selection, select

if TYPE_CHECKING:
    from .emission import SinkSlot
@dataclass(eq=False)
class Binding:
    """Unserialized, same-owner admission; configuration never comes from events."""

    owner: CommandRoutes
    selection: Selection
    generation: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: str = ""
    pipe: Any = None
    started: bool = False

    def __post_init__(self) -> None:
        self.state = self.selection.state

    def offer(self, event: dict[str, Any]) -> None:
        """Offer through the original owner; never fall back to a legacy sink."""
        self.owner.offer(self, event)


class CommandRoutes:
    """One source lifetime, one existing writer, at most four receiver generations."""

    def __init__(self, slot: SinkSlot, *, source: str = "agent-bridge") -> None:
        if source == "agent-bridge":
            from .bridge_projection import offered_fields, project
            schema = "bridge.lifecycle.v1"
        elif source == "agent-dispatch":
            from .dispatch_projection import offered_fields, project
            schema = "dispatch.lifecycle.v1"
        else:
            raise ValueError("unregistered telemetry source")
        self.source = source
        self.source_schema = schema
        self._offered_fields = offered_fields
        self._project = project
        self.slot = slot
        self.source_instance = str(uuid.uuid4())
        self._sequence = count(1)
        self._offer_lock = threading.Lock()
        self.bindings: list[Binding] = []
        self.writer: BufferedSink | None = None
        self.closed = False
        self._shutdown_deadline = float("inf")
        self._counts = {name: _Counter() for name in (
            "offered", "enqueued", "dropped_invalid", "dropped_full",
            "dropped_route", "serialization_failed", "pipe_writes",
            "start_failed", "write_failed", "abandoned",
            "receiver_exited", "observation_failed", "cleanup_unconfirmed",
        )}

    def admit(self, root: str | None) -> Binding:
        """Snapshot a trusted owner's explicit root during ordinary admission."""
        if len(self.bindings) >= 4:
            return Binding(self, Selection("route_limit"))
        selection = select(root) if root is not None else Selection("origin_unavailable")
        binding = Binding(self, selection)
        if root is None:
            return binding
        if self.closed:
            binding.state = "closed"
            return binding
        self.bindings.append(binding)
        if binding.state == "active" and self.writer is None:
            try:
                self.writer = self.slot.command_buffer(self._write, on_iteration=self._maintain)
            except RuntimeError:
                binding.state = "unsupported_contract"
        return binding

    def offer(self, binding: Binding, event: dict[str, Any]) -> None:
        next(self._counts["offered"])
        if not self._offer_lock.acquire(blocking=False):
            next(self._sequence)
            next(self._counts["dropped_full"])
            return
        try:
            self._offer_locked(binding, event)
        finally:
            self._offer_lock.release()

    def _offer_locked(self, binding: Binding, event: dict[str, Any]) -> None:
        sequence = next(self._sequence)
        if (
            self.closed or binding.owner is not self or binding.state != "active"
            or sequence > 2**53 - 1 or self.writer is None
        ):
            next(self._counts["dropped_route"])
            return
        try:
            filtered = self._offered_fields(event)
        except (ValueError, RuntimeError):
            next(self._counts["dropped_invalid"])
            return
        result = self.writer.offer({
            "event": filtered, "route": binding.generation,
            "sequence": sequence, "observed_ms": int(time.time() * 1000),
        })
        counter = {
            "accepted": "enqueued", "invalid": "dropped_invalid",
            "full": "dropped_full", "contended": "dropped_full", "inactive": "abandoned",
        }[result]
        next(self._counts[counter])

    def _write(self, record: dict[str, Any]) -> None:
        binding = next((b for b in self.bindings if b.generation == record["route"]), None)
        if binding is None or binding.state != "active":
            next(self._counts["dropped_route"])
            return
        try:
            event = self._project(record["event"])
        except ValueError:
            next(self._counts["dropped_invalid"])
            return
        try:
            envelope = {
                "contract": "consumer-telemetry-command", "version": 1,
                "source": self.source, "source_schema": self.source_schema,
                "source_instance": self.source_instance,
                "route_generation": binding.generation, "sequence": record["sequence"],
                "observed_at": datetime.fromtimestamp(
                    record["observed_ms"] / 1000, timezone.utc,
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "event": event,
            }
            encoded = (json.dumps(envelope, ensure_ascii=False, allow_nan=False,
                                  separators=(",", ":")) + "\n").encode("utf-8")
            if len(encoded) > 8192:
                raise ValueError("frame limit")
        except (ValueError, UnicodeError, OverflowError):
            next(self._counts["serialization_failed"])
            return
        try:
            if binding.pipe is None:
                from agent_procutil.command_pipe import CommandPipe

                binding.pipe = CommandPipe()
                if self.closed:
                    binding.pipe.deadline = min(
                        self._shutdown_deadline, time.monotonic() + .25,
                    )
                binding.pipe.start(
                    list(binding.selection.argv), binding.selection.root,
                    dict(binding.selection.environment),
                )
                binding.started = True
            binding.pipe.write(encoded)
        except Exception as error:
            binding.state = "deadline_exceeded" if isinstance(
                error, (TimeoutError, subprocess.TimeoutExpired),
            ) else (
                "start_failed" if not binding.started
                else "write_failed"
            )
            next(self._counts["write_failed" if binding.started else "start_failed"])
            self._retire(binding, min(self._shutdown_deadline, time.monotonic() + 1.5))
        else:
            next(self._counts["pipe_writes"])

    def _retire(self, binding: Binding, deadline: float) -> None:
        if binding.pipe is not None:
            try:
                binding.pipe.close(deadline)
            except Exception:
                binding.state = "cleanup_unconfirmed"
                next(self._counts["cleanup_unconfirmed"])

    def _maintain(self) -> None:
        if self.closed:
            return
        for binding in self.bindings:
            if binding.state != "active" or binding.pipe is None:
                continue
            deadline = min(self._shutdown_deadline, time.monotonic() + 1.5)
            try:
                exited = binding.pipe.exited()
            except Exception:
                next(self._counts["observation_failed"])
                binding.state = "cleanup_unconfirmed"
                self._retire(binding, deadline)
                if binding.pipe.closed:
                    binding.state = "write_failed"
                continue
            if exited:
                next(self._counts["receiver_exited"])
                binding.state = "write_failed"
                self._retire(binding, deadline)

    def close(self) -> None:
        """Explicit off-path shutdown; aggregate two-second deadline wins."""
        first_close = not self.closed
        self.closed = True
        deadline = time.monotonic() + 2
        self._shutdown_deadline = deadline
        for binding in self.bindings:
            if binding.pipe is not None:
                binding.pipe.deadline = time.monotonic() + .25
        if self.writer is not None:
            if first_close:
                self.writer.drain(.25)
            self.writer.close()
            if not self.writer.join(max(0, deadline - time.monotonic() - .25)):
                for binding in self.bindings:
                    if binding.state == "active":
                        binding.state = "cleanup_unconfirmed"
                return
        for binding in self.bindings:
            self._retire(binding, deadline)
            if binding.state == "active" or (
                binding.state == "cleanup_unconfirmed"
                and (binding.pipe is None or binding.pipe.closed)
            ):
                binding.state = "closed"

    def stats(self) -> dict[str, int]:
        """Content-free observations; pipe completion is never persistence."""
        result = {key: value.read() for key, value in self._counts.items()}
        if self.writer is not None:
            result["abandoned"] += self.writer.stats()["abandoned"]
        return result
