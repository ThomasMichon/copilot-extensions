"""Watch-owner installation-boundary state and atomic persistence."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

_ATOMIC_REPLACE_LOCK = threading.RLock()
_SUBSCRIPTIONS_SCHEMA = "agent-pull-requests.watch-subscriptions"
# An ordered admission epoch of this executing implementation, not a payload
# stamp or package version. Advance when older executors must not reactivate.
WATCH_ENGINE_GENERATION = 1


class VersionedSubscriptions(list[dict[str, Any]]):
    """Selected legacy entries that require complete, strict restoration."""


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


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate persisted watch subscriptions key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("invalid persisted watch subscriptions JSON constant")


def _read_subscriptions_document(path: Path) -> tuple[str | None, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, []
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except ValueError:
        raise ValueError("invalid persisted watch subscriptions JSON") from None
    return raw, data


def _subscription_entries(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        if data.get("schema") != _SUBSCRIPTIONS_SCHEMA:
            raise ValueError("unsupported persisted watch subscriptions schema")
        if type(data.get("version")) is not int or data["version"] <= 0:
            raise ValueError("invalid persisted watch subscriptions contract version")
        if data["version"] != 2 or not {
            "activeStateVersion", "engineGeneration", "stateByVersion",
        } <= data.keys():
            raise ValueError("unsupported persisted watch subscriptions contract version")
        generation = data["engineGeneration"]
        if type(generation) is not int or generation <= 0:
            raise ValueError("invalid persisted watch subscriptions engine generation")
        if generation > WATCH_ENGINE_GENERATION:
            raise ValueError("persisted watch subscriptions require a newer engine generation")
        active = data["activeStateVersion"]
        if type(active) is not int or active != 1:
            raise ValueError("unsupported persisted watch subscriptions active state version")
        versions = data["stateByVersion"]
        if not isinstance(versions, dict) or any(
            not key.isascii() or not key.isdecimal() or key.startswith("0") for key in versions
        ):
            raise ValueError("invalid persisted watch subscriptions version partitions")
        selected = versions.get(str(active))
        if not isinstance(selected, dict) or not isinstance(selected.get("subscriptions"), list):
            raise ValueError("invalid persisted watch subscriptions selected partition")
        return VersionedSubscriptions(selected["subscriptions"])
    if not isinstance(data, list):
        raise ValueError("invalid persisted watch subscriptions format")
    return data


def _read_subscriptions_state(path: Path) -> list[dict[str, Any]]:
    return _subscription_entries(_read_subscriptions_document(path)[1])


def read_subscriptions_state(root: Path | None = None) -> list[dict[str, Any]]:
    with _ATOMIC_REPLACE_LOCK:
        return _read_subscriptions_state(subscriptions_path(root))


def write_subscriptions_state(entries: list[dict[str, Any]], root: Path | None = None) -> None:
    path = subscriptions_path(root)

    def checked_document() -> tuple[str | None, Any]:
        try:
            raw, document = _read_subscriptions_document(path)
            _subscription_entries(document)
        except ValueError as exc:
            raise StateWriteRefused(str(exc)) from None
        return raw, document

    with _ATOMIC_REPLACE_LOCK:
        original, document = checked_document()
        if isinstance(document, dict):
            # Only the admitted partition changes; unknown retained versions and
            # selected-partition extensions never pass through an older serializer.
            document["stateByVersion"]["1"]["subscriptions"] = entries
        else:
            document = entries

        def check_existing_contract() -> None:
            current, _ = checked_document()
            if current != original:
                raise StateWriteRefused("persisted watch subscriptions changed before replacement")

        _atomic_write_json(path, document, before_replace=check_existing_contract)


def read_lock_data() -> dict[str, Any] | None:
    try:
        data = json.loads(lock_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_lock_data(data: dict[str, Any]) -> None:
    _atomic_write_json(lock_path(), data)
