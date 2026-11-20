"""Server-owned caller-to-harness admission for the optional portable receiver."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from weakref import WeakValueDictionary
from typing import Any

from process_telemetry.command import Binding, CommandRoutes
from process_telemetry.command_config import CAPABILITY
from process_telemetry.emission import LEGACY


def metadata_root() -> Path:
    """Return metadata beside this selected runtime's executing package."""
    return Path(__file__).resolve().parent / "metadata"


class ConsumerTelemetry:
    """Snapshot only roots approved in Bridge's trusted service configuration."""

    def __init__(self, harnesses: dict[str, str] | None) -> None:
        self.legacy = harnesses is None
        self.owner: CommandRoutes | None = None
        self.bindings: dict[str, Binding] = {}
        self.unavailable: Binding | None = None
        self.refusal: str | None = None
        self._sessions: WeakValueDictionary[str, Any] = WeakValueDictionary()
        if self.legacy:
            return
        assert harnesses is not None
        from .telemetry import _slot

        try:
            self.owner = _slot.configure_commands()
            self.unavailable = self.owner.admit(None)
            for index, (caller, root) in enumerate(harnesses.items()):
                if index == 4:
                    self.refusal = "route_limit"
                    break
                self.bindings[caller] = self.owner.admit(root)
        except Exception:
            # A second owner or unsupported optional runtime cannot affect work.
            self.bindings.clear()
            self.refusal = "unsupported_contract"

    def bind(self, session: Any) -> None:
        """Bind once to the admitted caller, never the target or a supplied path."""
        binding = LEGACY if self.legacy else self.bindings.get(
            session.caller_id, self.unavailable,
        )
        session._consumer_telemetry_binding = binding
        self._sessions[session.session_id] = session
        if session.event_log is not None:
            session.event_log.bind_consumer_telemetry(binding)

    def binding_for_log(self, session_id: str | None) -> Any:
        """Retain session admission across whole-log replacement without a new lookup."""
        session = self._sessions.get(session_id) if session_id is not None else None
        return (
            session._consumer_telemetry_binding if session is not None
            else LEGACY if self.legacy else self.unavailable
        )

    @contextmanager
    def admission(self, db: Any) -> Iterator[None]:
        """Mark missing log context unavailable before product initialization."""
        setattr(db, "_consumer_telemetry_owner", self)
        try:
            yield
        except BaseException:
            with suppress(Exception):
                self.close()
            raise

    def close(self) -> None:
        """Settle only this manager's optional command route."""
        if self.owner is not None:
            self.owner.close()

    def status(self) -> dict[str, Any]:
        """Expose bounded content-free selection and delivery observations."""
        return {
            "legacy": self.legacy,
            "refusal": self.refusal,
            "states": [binding.state for binding in self.bindings.values()],
            "counters": self.owner.stats() if self.owner is not None else {},
        }


def bind(manager: Any, session: Any) -> None:
    """Use the admitted manager owner; direct legacy managers keep their baseline."""
    owner = getattr(manager, "_consumer_telemetry", None)
    if owner is not None:
        owner.bind(session)


def close(manager: Any) -> None:
    """Close optional export without changing product shutdown outcomes."""
    owner = getattr(manager, "_consumer_telemetry", None)
    if owner is not None:
        try:
            owner.close()
        except Exception:
            pass


def capability() -> dict[str, Any]:
    """Return a fresh running-provider descriptor, independent of package version."""
    return {key: list(value) if type(value) is list else value for key, value in CAPABILITY.items()}
