"""Explicit configuration adapter over the existing retained sink and buffer."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .diagnostic_logs import Event, location

if TYPE_CHECKING:
    from .emission import SinkSlot
    from .buffer import BufferedSink


class _Logger(Protocol):
    def warning(self, message: str) -> object: ...


class _StoreStatus(Protocol):
    @property
    def last_error(self) -> str | None: ...


class _RetainedSink(Protocol):
    @property
    def store(self) -> _StoreStatus: ...

    def __call__(self, event: Event) -> None: ...
    def maintain(self) -> None: ...


def retained_directory(data: Event) -> Path:
    options = data["retained_spool"]
    if "sink" in data or "spool" in data:
        raise ValueError("mixed telemetry modes")
    if type(options) is not dict or set(options) != {"directory"}:
        raise ValueError("invalid retained options")
    value = options["directory"]
    if type(value) is not str or not value.strip() or "\0" in value:
        raise ValueError("invalid retained directory")
    directory = Path(value)
    if not directory.is_absolute():
        raise ValueError("retained directory must be absolute")
    return directory


def _warn(logger: _Logger) -> None:
    try:
        logger.warning("configured retained telemetry unavailable")
    except Exception:
        pass


class _ConfiguredSpool:
    def __init__(
        self, directory: Path, factory: Callable[[Path], _RetainedSink], logger: _Logger,
    ) -> None:
        self.directory = directory
        self.factory = factory
        self.logger = logger
        self.sink: _RetainedSink | None = None

    def start(self) -> None:
        # Runs only on the existing buffer's worker, never the serving producer.
        try:
            namespace = self.directory / "retained"
            if namespace.is_symlink() or not namespace.is_dir():
                raise ValueError("retained namespace not provisioned")
            self.sink = self.factory(self.directory)
            if self.sink.store.last_error is not None:
                _warn(self.logger)
        except Exception:
            _warn(self.logger)
            raise RuntimeError("retained initialization failed") from None

    def __call__(self, event: Event) -> None:
        if self.sink is None:
            raise RuntimeError("retained initialization unavailable")
        self.sink(event)

    def maintain(self) -> None:
        if self.sink is not None:
            self.sink.maintain()


def install_retained(
    data: Event, slot: SinkSlot, factory: Callable[[Path], _RetainedSink], logger: _Logger,
) -> BufferedSink | None:
    """Return the selected owned buffer, or fail open without unsafe retries."""
    try:
        directory = retained_directory(data)
        owner = _ConfiguredSpool(directory, factory, logger)
        slot.set(None)
        return slot.set_buffered(
            owner, on_start=owner.start, on_iteration=owner.maintain,
            stamp_missing_ts=True,
        )
    except Exception:
        _warn(logger)
        return None


def close_configured(slot: SinkSlot) -> None:
    """Check current ownership and close under the existing configuration lock."""
    try:
        with slot._configuration_lock:
            writer = slot._owned_buffer
            if writer is not None and isinstance(writer._sink, _ConfiguredSpool):
                slot.set(None)
    except Exception:
        pass  # optional cleanup never replaces a primary service outcome


def configured_inventory(
    path: str | Path, *, component: str, service: str,
) -> dict[str, object]:
    """Read configuration/metadata only; never import a configured factory."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"configuration": "absent", "write_health": "unknown-from-discovery"}
    except (OSError, ValueError, UnicodeError):
        return {"configuration": "unreadable-or-invalid",
                "write_health": "unknown-from-discovery"}
    if not isinstance(data, dict) or "retained_spool" not in data:
        return {"configuration": "legacy-or-custom",
                "write_health": "unknown-from-discovery"}
    try:
        directory = retained_directory(data) / "retained"
    except (ValueError, TypeError, KeyError):
        return {"configuration": "invalid-retained",
                "write_health": "unknown-from-discovery"}
    try:
        entry = location(directory, component, service)
        provisioned = directory.is_dir() and not directory.is_symlink()
    except OSError:
        entry = {"owner": service, "active": str(directory / f"{component}.jsonl"),
                 "retention": "30-day-record-age", "metadata": "unavailable"}
        provisioned = None
    return {
        **entry,
        "configuration": "retained-spool",
        "maintenance": "owned buffer startup and busy/idle iteration",
        "namespace_preprovisioned": provisioned,
        "write_health": "unknown-from-discovery",
    }
