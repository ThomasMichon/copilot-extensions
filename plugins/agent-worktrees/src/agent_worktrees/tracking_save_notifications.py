"""Process-local optional subscribers drained only outside record/pair holds."""

from __future__ import annotations

import inspect
import logging
import os
import threading
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from machine_transport import LocalExecutionContext

from . import tracking_save_capture as capture

if TYPE_CHECKING:
    from .tracking import WorktreeRecord

logger = logging.getLogger(__name__)
MAX_PENDING = 32
MAX_PENDING_BYTES = 1024 * 1024
MAX_SUBSCRIBERS = 32
MAX_DRAIN = 64
MAX_CHANGED_PATHS = 32
MAX_COUNTER = (1 << 63) - 1
ReceiptEvent = capture.SaveReceipt | capture.DeliveryIssue
ReceiptObserver = Callable[[ReceiptEvent], object]
LegacyObserver = Callable[[Path, "WorktreeRecord"], object]
_registry_lock = threading.Lock()
_legacy: dict[LegacyObserver, None] = {}
_receipts: dict[ReceiptObserver, None] = {}
_local = threading.local()


@dataclass(frozen=True, slots=True)
class Prepared:
    before: capture.Projection
    observers: tuple[ReceiptObserver, ...]
    record_held: bool
    sidecar_held: bool


@dataclass(frozen=True, slots=True)
class Pending:
    path: Path
    snapshot: capture.FrozenValue | None
    receipt: ReceiptEvent | None
    legacy: tuple[LegacyObserver, ...]
    observers: tuple[ReceiptObserver, ...]
    size: int


@dataclass
class _State:
    holds: int = 0
    records: int = 0
    paths: dict[str, int] = field(default_factory=dict)
    pending: deque[Pending] = field(default_factory=deque)
    size: int = 0
    delivering: bool = False
    omitted_count: int = 0
    omitted_bytes: int = 0
    saturated: bool = False
    unknown_bytes: bool = False
    changed_paths: dict[str, None] = field(default_factory=dict)
    changed_all: bool = False


@dataclass
class Hold:
    state: _State
    thread_id: int
    path: str | None
    released: bool = False


def _state() -> _State:
    state = getattr(_local, "state", None)
    if state is None:
        state = _local.state = _State()
    return state


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def capture_idle() -> bool:
    """Whether a new optional compute scope can exclude earlier deferred saves."""
    state = _state()
    return not (
        state.holds or state.pending or state.delivering or state.omitted_count
        or state.changed_paths or state.changed_all
    )


def worktree_changed(worktree_path: str) -> None:
    """Defer small native wakes until every record and pair hold is released."""
    state = _state()
    if not isinstance(worktree_path, str) or not worktree_path:
        return
    try:
        oversized = len(worktree_path.encode("utf-8")) > 4096
    except UnicodeError:
        oversized = True
    if oversized or (
        worktree_path not in state.changed_paths and len(state.changed_paths) >= MAX_CHANGED_PATHS
    ):
        state.changed_paths.clear()
        state.changed_all = True
    elif not state.changed_all:
        state.changed_paths[worktree_path] = None
    _drain(state)


def acquire(*, path: Path | None = None) -> Hold:
    state = _state()
    key = None if path is None else _key(path)
    token = Hold(state, threading.get_ident(), key)
    if key is not None:
        state.paths[key] = state.paths.get(key, 0) + 1
    state.holds += 1
    state.records += int(key is not None)
    return token


def release(token: Hold) -> None:
    if token.released or token.thread_id != threading.get_ident() or token.state.holds <= 0:
        raise RuntimeError("invalid committed-save notification hold release")
    token.released = True
    token.state.holds -= 1
    token.state.records -= int(token.path is not None)
    if token.path is not None:
        token.state.paths[token.path] -= 1
        if not token.state.paths[token.path]:
            token.state.paths.pop(token.path)
    _drain(token.state)


@contextmanager
def hold() -> Iterator[None]:
    token = acquire()
    try:
        yield
    finally:
        release(token)


def _register(callback: Callable, registry: dict) -> Callable[[], None]:
    if not callable(callback) or inspect.iscoroutinefunction(callback):
        raise TypeError("committed-save observers must be synchronous callables")
    with _registry_lock:
        if callback not in registry and len(registry) >= MAX_SUBSCRIBERS:
            raise ValueError("committed-save subscriber bound reached")
        registry[callback] = None

    def unsubscribe() -> None:
        with _registry_lock:
            registry.pop(callback, None)

    return unsubscribe


def observe_writes(callback: LegacyObserver) -> Callable[[], None]:
    return _register(callback, _legacy)


def observe_receipts(callback: ReceiptObserver) -> Callable[[], None]:
    """Opt into selected evidence, with explicit omission/delivery issues."""
    return _register(callback, _receipts)


def prepare(record: WorktreeRecord | None, path: Path) -> Prepared | None:
    from . import tracking

    with _registry_lock:
        observers = tuple(_receipts)
    if not observers:
        return None
    # Optional detachment cannot reinterpret an eventual canonical write error.
    try:
        before = capture.project(record)
    except Exception:
        logger.exception("Optional before-save evidence capture failed")
        before = capture.omitted("before-capture-failed")
    return Prepared(
        before,
        observers,
        _state().paths.get(_key(path), 0) > 0,
        tracking._thread_sidecar_counts().get(_key(path), 0) > 0,
    )


def _omit(state: _State, count: int, size: int | None, reason: str) -> None:
    logger.warning(
        "Committed-save notification delivery incomplete: %s (%s delivery incidents, %s bytes)",
        reason,
        count,
        "unknown" if size is None else size,
    )
    state.unknown_bytes |= size is None
    size = 0 if size is None else size
    state.saturated |= (
        state.omitted_count + count > MAX_COUNTER or state.omitted_bytes + size > MAX_COUNTER
    )
    state.omitted_count = min(MAX_COUNTER, state.omitted_count + count)
    state.omitted_bytes = min(MAX_COUNTER, state.omitted_bytes + size)


def committed(
    path: Path,
    content: str | None,
    prepared: Prepared | None,
    *,
    legacy_snapshot: capture.FrozenValue | None = None,
    legacy_bytes: int = 0,
    reporting_context: LocalExecutionContext | None = None,
) -> None:
    with _registry_lock:
        legacy = tuple(_legacy)
    observers = () if prepared is None else prepared.observers
    if not legacy and not observers:
        return
    raw = None
    try:
        if content is None:
            raise capture.CaptureOmitted("saved-content-unavailable")
        raw = capture.content_bytes(content)
    except (capture.CaptureOmitted, UnicodeError):
        logger.warning(
            "Committed-save evidence/legacy snapshot omitted: saved content exceeds bounds"
        )
        content = None
    result = None
    if prepared is not None:
        try:
            after = (
                capture.project(capture.parse(content, path, reporting_context=reporting_context))
                if content is not None
                else capture.omitted("saved-content-unavailable-or-oversized")
            )
        except Exception:
            logger.exception("Optional committed-save after evidence capture failed")
            after = capture.omitted("after-capture-failed")
        try:
            from . import tracking

            result = capture.receipt(
                uuid.uuid4().hex,
                path,
                prepared.before,
                after,
                raw,
                record_held=prepared.record_held and _state().paths.get(_key(path), 0) > 0,
                sidecar_held=prepared.sidecar_held
                and (tracking._thread_sidecar_counts().get(_key(path), 0) > 0),
            )
            if result.coverage == "partial":
                logger.warning(
                    "Committed-save selected evidence is partial: %s", result.receipt_id
                )
        except Exception:
            logger.exception("Optional committed-save receipt capture failed")
    state = _state()
    if legacy and legacy_snapshot is None:
        _omit(state, 1, None, "legacy-snapshot-unavailable")
        legacy = ()
    size = (
        len(os.fsencode(path))
        + (0 if not legacy else legacy_bytes)
        + (0 if result is None else result.encoded_bytes)
        + 8 * (len(legacy) + len(observers))
    )
    if len(state.pending) >= MAX_PENDING or state.size + size > MAX_PENDING_BYTES:
        _omit(state, 1, size, "queue-capacity")
    elif result is not None or legacy:
        state.pending.append(
            Pending(path, legacy_snapshot if legacy else None, result, legacy, observers, size)
        )
        state.size += size
    elif observers:
        _omit(state, 1, size, "receipt-unavailable")
    _drain(state)


def _notify(callback: Callable, *arguments: object) -> None:
    # This boundary covers only optional subscribers, never persistence or lock release.
    try:
        result = callback(*arguments)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise TypeError("committed-save subscriber returned an awaitable")
    except Exception:
        logger.exception("Optional committed-save subscriber failed after canonical persistence")


def _resume(item: Pending) -> None:
    state = _state()
    if len(state.pending) >= MAX_PENDING or state.size + item.size > MAX_PENDING_BYTES:
        _omit(state, 1, item.size, "held-callback-continuation-bound")
    else:
        state.pending.appendleft(item)
        state.size += item.size


def _deliver(item: Pending) -> None:
    if item.receipt is not None:
        for index, callback in enumerate(item.observers):
            _notify(callback, item.receipt)
            if _state().holds:
                _resume(
                    Pending(
                        item.path,
                        item.snapshot,
                        item.receipt,
                        item.legacy,
                        item.observers[index + 1 :],
                        item.size,
                    )
                )
                return
    for index, callback in enumerate(item.legacy):
        try:
            if item.snapshot is None:
                raise capture.CaptureOmitted("legacy-content-unavailable")
            record = capture.thaw_legacy(item.snapshot)
        except Exception:
            logger.exception("Optional legacy committed-save snapshot decoding failed")
            _omit(_state(), 1, item.size, "legacy-decode-failed")
            continue
        _notify(callback, item.path, record)
        if _state().holds:
            _resume(
                Pending(
                    item.path,
                    item.snapshot,
                    None,
                    item.legacy[index + 1 :],
                    (),
                    item.size,
                )
            )
            return


def _drain(state: _State) -> None:
    if state.holds or state.delivering:
        return
    state.delivering = True
    try:
        delivered = 0
        while state.pending and not state.holds and delivered < MAX_DRAIN:
            item = state.pending.popleft()
            state.size -= item.size
            delivered += 1
            _deliver(item)
        if state.pending and not state.holds:
            _omit(state, len(state.pending), state.size, "reentrant-drain-bound")
            state.pending.clear()
            state.size = 0
        if state.omitted_count and not state.holds:
            issue = capture.DeliveryIssue(
                "incomplete-process-local-delivery",
                state.omitted_count,
                None if state.unknown_bytes else state.omitted_bytes,
                state.saturated,
            )
            state.omitted_count = state.omitted_bytes = 0
            state.saturated = False
            state.unknown_bytes = False
            with _registry_lock:
                observers = tuple(_receipts)
            _deliver(Pending(Path("."), None, issue, (), observers, 512 + 8 * len(observers)))
            if state.pending and not state.holds:
                _omit(state, len(state.pending), state.size, "reentrant-omission-callback-bound")
                state.pending.clear()
                state.size = 0
        if not state.holds and (state.changed_paths or state.changed_all):
            paths = [None] if state.changed_all else list(state.changed_paths)
            state.changed_paths.clear()
            state.changed_all = False
            try:
                from .worktree_notifications import notify_worktree_changed

                for index, path in enumerate(paths):
                    if state.holds:
                        if path is None:
                            state.changed_all = True
                            state.changed_paths.clear()
                        else:
                            for remaining in paths[index:]:
                                worktree_changed(remaining)
                        break
                    notify_worktree_changed(path)
            except Exception:
                logger.exception("Native worktree wake failed after canonical persistence")
    finally:
        state.delivering = False
