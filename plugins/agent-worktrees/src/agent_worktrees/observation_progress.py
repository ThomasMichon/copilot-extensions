"""Owner-scoped progress and completed inventory observations."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps

NativeInventory = dict[str, tuple[int, str]]

_progress: ContextVar[Callable[[], None] | None] = ContextVar(
    "worktrees_observation_progress", default=None,
)


@contextmanager
def progress_scope(callback: Callable[[], None] | None) -> Iterator[None]:
    token = _progress.set(callback)
    try:
        yield
    finally:
        _progress.reset(token)


def observation_boundary() -> None:
    callback = _progress.get()
    if callback is not None:
        callback()


@dataclass
class InventoryCapture:
    completed: NativeInventory | None = None


_inventory: ContextVar[InventoryCapture | None] = ContextVar(
    "worktrees_native_inventory_capture", default=None,
)


@contextmanager
def inventory_scope() -> Iterator[InventoryCapture]:
    capture = InventoryCapture()
    token = _inventory.set(capture)
    try:
        yield capture
    finally:
        _inventory.reset(token)


def capture_inventory(
    collect: Callable[[str], NativeInventory | None],
) -> Callable[[str], NativeInventory | None]:
    @wraps(collect)
    def observe(mux_bin: str) -> NativeInventory | None:
        result = collect(mux_bin)
        capture = _inventory.get()
        if capture is not None:
            capture.completed = dict(result) if result is not None else None
        return result

    return observe
