"""Watch-owner installation-boundary state and atomic persistence."""

from __future__ import annotations

import json
import math
import os
import stat
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

_ATOMIC_REPLACE_LOCK = threading.RLock()
_SUBSCRIPTIONS_SCHEMA = "agent-pull-requests.watch-subscriptions"
# An ordered admission epoch of this executing implementation, not a payload
# stamp or package version. Advance when older executors must not reactivate.
WATCH_ENGINE_GENERATION = 1
TransitionPhase = Literal["absent", "prepared", "sealed-legacy", "import-ready", "active"]
_TRANSITION_PHASES = {"prepared", "sealed-legacy", "import-ready", "active"}
_REPARSE_POINT = 0x400
_MAX_METADATA_BYTES = 1_048_576


@dataclass(frozen=True)
class WatchStatePaths:
    legacy_subscriptions: Path
    historical_lock: Path
    retained_lock: Path
    staged_barrier: Path
    preparation_pin: Path
    journal: Path
    isolated_subscriptions: Path
    writer_lock: Path


def watch_state_paths(root: Path | None = None) -> WatchStatePaths:
    base = state_dir() if root is None else root
    historical_lock = base / "agent-pull-requests-watch.lock"
    return WatchStatePaths(
        legacy_subscriptions=base / "watch-subscriptions.json",
        historical_lock=historical_lock,
        retained_lock=base / "watch-state-transition.legacy.lock",
        staged_barrier=base / "watch-state-transition.barrier",
        preparation_pin=base / "watch-state-transition.pin",
        journal=base / "watch-state-transition.json",
        isolated_subscriptions=base / "watch-state" / "subscriptions.json",
        writer_lock=historical_lock / "writer.lock",
    )


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
    return watch_state_paths(root).legacy_subscriptions


def _regular_bytes(path: Path, *, max_bytes: int | None = _MAX_METADATA_BYTES) -> bytes | None:
    """Read stable plain bytes; None preserves legacy subscription size policy."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    ):
        raise PermissionError("watch journal metadata must be a regular non-link file")
    if max_bytes is not None and info.st_size > max_bytes:
        raise ValueError("watch journal metadata exceeds its bounded size")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(info, opened):
            raise PermissionError("watch journal metadata changed before its regular-file read")
        content = handle.read() if max_bytes is None else handle.read(max_bytes + 1)
        finished = os.fstat(handle.fileno())
    current = path.lstat()
    identities = [
        (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        for item in (info, opened, finished, current)
    ]
    # Windows can expose creation time via lstat and change time via fstat.
    metadata_stable = all(
        (before.st_mode, before.st_ctime_ns) == (after.st_mode, after.st_ctime_ns)
        for before, after in ((info, current), (opened, finished))
    )
    if (
        (max_bytes is not None and len(content) > max_bytes)
        or any(item != identities[0] for item in identities[1:])
        or not metadata_stable
        or not stat.S_ISREG(current.st_mode)
        or getattr(current, "st_file_attributes", 0) & _REPARSE_POINT
    ):
        raise PermissionError("watch journal metadata changed during its bounded read")
    return content


def read_transition_phase(
    root: Path | None = None, *, allow_preparation_pin: bool = False,
) -> TransitionPhase:
    """Observe a marker, never establish physical sealing, custody or authority."""
    paths = watch_state_paths(root)
    try:
        raw = _regular_bytes(paths.journal)
    except (OSError, ValueError) as exc:
        raise ValueError("watch state transition journal cannot be read safely") from exc
    if raw is not None:
        try:
            data = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            if (
                not isinstance(data, dict)
                or data.get("schema") != "agent-pull-requests.watch-state-transition"
                or type(data.get("version")) is not int or data["version"] not in {1, 2}
                or not isinstance(data.get("phase"), str)
                or data["phase"] not in _TRANSITION_PHASES
                or (data["version"] == 2 and data["phase"] not in {"prepared", "sealed-legacy"})
            ):
                raise ValueError("unsupported marker")
        except (ValueError, UnicodeError):
            raise ValueError("invalid watch state transition journal") from None
        return cast(TransitionPhase, data["phase"])
    for path in (
        paths.historical_lock, paths.retained_lock, paths.staged_barrier,
        paths.isolated_subscriptions.parent, paths.preparation_pin,
    ):
        try:
            info = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if (
            (path != paths.historical_lock and not (
                allow_preparation_pin and path == paths.preparation_pin
            ))
            or not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
        ):
            raise ValueError("watch state transition evidence has no journal")
    return "absent"


def _require_legacy_state(root: Path | None) -> None:
    phase = read_transition_phase(root)
    if phase != "absent":
        raise ValueError(f"watch state transition requires guarded recovery ({phase})")


def _atomic_write_json(
    path: Path, data: object, *, before_replace: Callable[[], None] | None = None,
    create_parent: bool = True,
) -> None:
    if create_parent:
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


def _round_trip_float(raw: str) -> float:
    try:
        value = float(raw)
        if not math.isfinite(value) or Decimal(raw) != Decimal(str(value)):
            raise ValueError("persisted watch subscriptions JSON number cannot round-trip")
    except ArithmeticError:
        raise ValueError("invalid persisted watch subscriptions JSON number") from None
    return value


def _read_subscriptions_document(
    path: Path, *, require_plain: bool = False,
) -> tuple[str | None, Any]:
    if require_plain:
        content = _regular_bytes(path, max_bytes=None)
        if content is None:
            return None, []
        raw = content.decode("utf-8")
    else:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None, []
    try:
        data = json.loads(
            raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant,
            parse_float=_round_trip_float,
        )
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


def _read_subscriptions_state(
    path: Path, *, require_plain: bool = False,
) -> list[dict[str, Any]]:
    return _subscription_entries(_read_subscriptions_document(path, require_plain=require_plain)[1])


def read_subscriptions_state(
    root: Path | None = None, *, require_plain: bool = False,
) -> list[dict[str, Any]]:
    with _ATOMIC_REPLACE_LOCK:
        _require_legacy_state(root)
        return _read_subscriptions_state(subscriptions_path(root), require_plain=require_plain)


def write_subscriptions_state(
    entries: list[dict[str, Any]], root: Path | None = None, *,
    validate: Callable[[list[dict[str, Any]]], None] | None = None,
    before_replace: Callable[[], None] | None = None,
    create_parent: bool = True,
    require_plain: bool = False,
) -> None:
    path = subscriptions_path(root)

    def checked_document() -> tuple[str | None, Any]:
        try:
            _require_legacy_state(root)
            raw, document = _read_subscriptions_document(path, require_plain=require_plain)
            selected = _subscription_entries(document)
            if isinstance(selected, VersionedSubscriptions):
                if validate is None:
                    raise ValueError("persisted watch subscriptions require registry validation")
                validate(selected)
        except ValueError as exc:
            raise StateWriteRefused(str(exc)) from None
        return raw, document

    with _ATOMIC_REPLACE_LOCK:
        original, document = checked_document()
        if isinstance(document, dict):
            # Only the admitted partition changes; unknown retained versions and
            # selected-partition extensions never pass through an older serializer.
            document["stateByVersion"]["1"]["subscriptions"] = entries
            assert validate is not None
            try:
                validate(VersionedSubscriptions(entries))
            except ValueError as exc:
                raise StateWriteRefused(str(exc)) from None
        else:
            document = entries

        def check_existing_contract() -> None:
            current, _ = checked_document()
            if current != original:
                raise StateWriteRefused("persisted watch subscriptions changed before replacement")
            if before_replace is not None:
                before_replace()

        if create_parent:
            _atomic_write_json(path, document, before_replace=check_existing_contract)
        else:
            _atomic_write_json(
                path, document, before_replace=check_existing_contract, create_parent=False,
            )


def read_lock_data() -> dict[str, Any] | None:
    try:
        data = json.loads(lock_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_lock_data(data: dict[str, Any]) -> None:
    _atomic_write_json(lock_path(), data)
