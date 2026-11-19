"""Watch-owner installation-boundary state and atomic persistence."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

_ATOMIC_REPLACE_LOCK = threading.Lock()
_SUBSCRIPTIONS_SCHEMA = "agent-pull-requests.watch-subscriptions"


class StateWriteRefused(OSError):
    """Existing subscription state cannot be safely replaced by this writer."""


class StateCommitUncertain(OSError):
    """Replacement committed, but its crash-durability acknowledgement failed."""


def state_dir() -> Path:
    override = os.environ.get("AGENT_PULL_REQUESTS_HOME", "").strip()
    return Path(override) if override else Path.home() / ".agent-pull-requests"


def lock_path() -> Path:
    return state_dir() / "watch-daemon.lock"


def subscriptions_path(root: Path | None = None) -> Path:
    return (state_dir() if root is None else root) / "watch-subscriptions.json"


def _atomic_write_json(
    path: Path, data: object, *, before_replace: Callable[[], None] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
        suffix=".tmp", delete=False,
    )
    tmp = Path(handle.name)
    try:
        with handle:
            json.dump(data, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        with _ATOMIC_REPLACE_LOCK:
            if before_replace is not None:
                before_replace()
            tmp.replace(path)
            if os.name != "nt":
                try:
                    directory = os.open(path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except OSError as exc:
                    raise StateCommitUncertain("state replacement durability is uncertain") from exc
    finally:
        tmp.unlink(missing_ok=True)


def _read_subscriptions_state(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        raise ValueError("invalid persisted watch subscriptions JSON") from None
    if isinstance(data, dict):
        if data.get("schema") != _SUBSCRIPTIONS_SCHEMA:
            raise ValueError("unsupported persisted watch subscriptions schema")
        if type(data.get("version")) is not int or data["version"] <= 0:
            raise ValueError("invalid persisted watch subscriptions contract version")
        # This expansion release still supports only the unversioned legacy list.
        raise ValueError("unsupported persisted watch subscriptions contract version")
    if not isinstance(data, list):
        raise ValueError("invalid persisted watch subscriptions format")
    return data


def read_subscriptions_state(root: Path | None = None) -> list[dict[str, Any]]:
    with _ATOMIC_REPLACE_LOCK:
        return _read_subscriptions_state(subscriptions_path(root))


def write_subscriptions_state(entries: list[dict[str, Any]], root: Path | None = None) -> None:
    path = subscriptions_path(root)

    def check_existing_contract() -> None:
        try:
            _read_subscriptions_state(path)
        except ValueError as exc:
            raise StateWriteRefused(str(exc)) from None

    _atomic_write_json(path, entries, before_replace=check_existing_contract)


def read_lock_data() -> dict[str, Any] | None:
    try:
        data = json.loads(lock_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_lock_data(data: dict[str, Any]) -> None:
    _atomic_write_json(lock_path(), data)
