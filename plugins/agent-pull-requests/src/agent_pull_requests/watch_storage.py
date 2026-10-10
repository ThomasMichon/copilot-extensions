"""Watch-owner installation-boundary state and atomic persistence."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

_ATOMIC_REPLACE_LOCK = threading.Lock()

class StateCommitUncertain(OSError):
    """Replacement committed, but its crash-durability acknowledgement failed."""


def state_dir() -> Path:
    override = os.environ.get("AGENT_PULL_REQUESTS_HOME", "").strip()
    return Path(override) if override else Path.home() / ".agent-pull-requests"


def lock_path() -> Path:
    return state_dir() / "watch-daemon.lock"


def subscriptions_path() -> Path:
    return state_dir() / "watch-subscriptions.json"


def _atomic_write_json(path: Path, data: object) -> None:
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


def read_subscriptions_state() -> list[dict]:
    try:
        raw = subscriptions_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        raise ValueError("invalid persisted watch subscriptions JSON") from None
    if not isinstance(data, list):
        raise ValueError("invalid persisted watch subscriptions format")
    return data


def write_subscriptions_state(entries: list[dict]) -> None:
    _atomic_write_json(subscriptions_path(), entries)


def read_lock_data() -> dict | None:
    try:
        data = json.loads(lock_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_lock_data(data: dict) -> None:
    _atomic_write_json(lock_path(), data)
