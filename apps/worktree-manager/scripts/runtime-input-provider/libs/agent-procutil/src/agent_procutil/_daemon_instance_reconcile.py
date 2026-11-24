"""Bounded collection over the owning store's instance and cursor operations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
import math
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Protocol, TypedDict

from .daemon_instances import DaemonInstanceReceipt, InstanceCleanupResult

if TYPE_CHECKING:
    from ._daemon_instance_store import _CustodyLock


class _Store(Protocol):
    @property
    def _ID(self) -> re.Pattern[str]: ...
    @property
    def _cursor_guard(self) -> _CustodyLock: ...
    def _monotonic_clock(self) -> float: ...
    def _wall_clock(self) -> float: ...
    def registry_root(self) -> Path: ...
    def _root(self, path: Path) -> Path: ...
    def _runtime_scope_key(self, runtime_root: str | os.PathLike[str]) -> bytes: ...
    def _inventory_exists(self, root: Path) -> bool: ...
    def _inventory_paths(self, root: Path) -> Iterator[Path]: ...
    def _read_cursor(self, root: Path, runtime_root: str | os.PathLike[str] | None) -> str: ...
    def _write_cursor(
        self, root: Path, key: str, runtime_root: str | os.PathLike[str] | None,
    ) -> None: ...
    def _file_lock(
        self, path: Path, *, guard: _CustodyLock, nonblocking: bool,
    ) -> AbstractContextManager[None]: ...
    def _read(self, path: Path) -> dict[str, str]: ...
    def _expire(self, record: dict[str, str], now: float) -> bool: ...
    def cleanup(
        self, receipt: DaemonInstanceReceipt, *, stop_timeout: float, terminate: bool,
        runtime_root: str | os.PathLike[str] | None, _caller_pid: int | None = None,
    ) -> InstanceCleanupResult: ...


class _CleanupOptions(TypedDict, total=False):
    stop_timeout: float
    terminate: bool
    runtime_root: str | os.PathLike[str] | None
    _caller_pid: int


def reconcile(
    store: _Store, root: str | os.PathLike[str] | None, limit: int, max_seconds: float = 5, *,
    _caller_pid: int | None = None, terminate: bool = False,
    runtime_root: str | os.PathLike[str] | None = None,
) -> list[InstanceCleanupResult]:
    started = store._monotonic_clock()
    if type(limit) is not int or not 1 <= limit <= 4096:
        raise ValueError("daemon reconciliation limit must be between 1 and 4096")
    if isinstance(max_seconds, bool) or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("daemon reconciliation deadline must be finite and positive")
    if type(terminate) is not bool:
        raise ValueError("daemon reconciliation termination policy must be boolean")
    scope_key = None
    if runtime_root is not None:
        runtime_root = Path(runtime_root)
        scope_key = store._runtime_scope_key(runtime_root)
        if (
            not runtime_root.is_absolute()
            or store._runtime_scope_key(runtime_root.resolve()) != scope_key
        ):
            raise ValueError("daemon reconciliation runtime scope must be absolute and unredirected")
    registry = Path(root) if root is not None else store.registry_root()
    if not store._inventory_exists(registry):
        return []
    store._root(registry)
    paths = sorted(store._inventory_paths(registry))
    cursor = store._read_cursor(registry, runtime_root)
    if not paths:
        return []
    if any(not store._ID.fullmatch(path.stem) for path in paths):
        raise ValueError("noncanonical inventory instance key")
    results = []
    deadline = started + max_seconds
    with store._file_lock(
        registry / ".reconcile.lock", guard=store._cursor_guard, nonblocking=True,
    ):
        cursor = store._read_cursor(registry, runtime_root)
        ordered = [path for path in paths if path.stem > cursor]
        ordered += [path for path in paths if path.stem <= cursor]
        visited = None
        try:
            for count, path in enumerate(ordered):
                if count >= limit:
                    break
                remaining = deadline - store._monotonic_clock()
                if remaining <= 0:
                    break
                visited = path.stem
                try:
                    candidate = store._read(path)
                    if scope_key is not None and (
                        store._runtime_scope_key(candidate["runtime_root"]) != scope_key
                    ):
                        continue
                    finite_production = (
                        candidate["purpose"] == "production"
                        and candidate.get("lifecycle") == "finite-worker"
                    )
                    if not finite_production and (
                        candidate["purpose"] == "production"
                        or not store._expire(candidate, store._wall_clock())
                    ):
                        continue
                except (OSError, ValueError, KeyError):
                    results.append(InstanceCleanupResult(path.stem, "held", "invalid-record"))
                    continue
                options: _CleanupOptions = {
                    "stop_timeout": min(5, remaining), "terminate": terminate,
                    "runtime_root": runtime_root,
                }
                if _caller_pid is not None:
                    options["_caller_pid"] = _caller_pid
                results.append(store.cleanup(DaemonInstanceReceipt(path.stem, registry), **options))
        finally:
            if visited is not None and visited != cursor:
                store._write_cursor(registry, visited, runtime_root)
    return results
