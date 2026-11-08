"""Bounded collection over the owning store's instance and cursor operations."""

import math
from pathlib import Path

from .daemon_instances import DaemonInstanceReceipt, InstanceCleanupResult


def reconcile(
    store, root, limit, max_seconds=5, *, _caller_pid=None, terminate=False, runtime_root=None,
):
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
    root = Path(root) if root is not None else store.registry_root()
    if not store._inventory_exists(root):
        return []
    store._root(root)
    paths = sorted(store._inventory_paths(root))
    cursor = store._read_cursor(root, runtime_root)
    if not paths:
        return []
    if any(not store._ID.fullmatch(path.stem) for path in paths):
        raise ValueError("noncanonical inventory instance key")
    results = []
    deadline = started + max_seconds
    with store._file_lock(
        root / ".reconcile.lock", guard=store._cursor_guard, nonblocking=True,
    ):
        cursor = store._read_cursor(root, runtime_root)
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
                options = {
                    "stop_timeout": min(5, remaining), "terminate": terminate,
                    "runtime_root": runtime_root,
                }
                if _caller_pid is not None:
                    options["_caller_pid"] = _caller_pid
                results.append(store.cleanup(DaemonInstanceReceipt(path.stem, root), **options))
        finally:
            if visited is not None and visited != cursor:
                store._write_cursor(root, visited, runtime_root)
    return results
