"""Mandatory, error-preserving retirement of the monitor's owned surfaces."""

from __future__ import annotations

import os
import time
import logging
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from .allocation_link_runtime import AllocationLinkRuntime
    from .handoff_prompt_receipt_control import ReceiptControl
    from .mux_link import InProcessRuntime as ManagedMuxRuntime
    from .repository_map import RepositoryMapRuntime
    from .status_monitor_cutover import ControlServer
    from .worktree_status_daemon import InProcessRuntime as WorktreeStatusRuntime
    from .worktrees_runtime import WorktreesRuntime

from . import locks, resident_push, status_monitor_cutover


class _CountedSurface(Protocol):
    def active_handler_count(self) -> int: ...


class _ReleaseGuard(Protocol):
    def release(self) -> object: ...


_Hook = TypeVar("_Hook", bound=_CountedSurface)
_Classifier = TypeVar("_Classifier", bound=_CountedSurface)
_Writer = TypeVar("_Writer", bound=_CountedSurface)


def prune_retired_surfaces(
    hooks: list[_Hook], classifiers: list[_Classifier], writers: list[_Writer],
    has_inflight_write: Callable[[], bool],
) -> None:
    """Forget only handler-free surfaces while the caller holds its state lock."""
    hooks[:] = [server for server in hooks if server.active_handler_count() > 0]
    classifiers[:] = [server for server in classifiers if server.active_handler_count() > 0]
    if not has_inflight_write():
        writers[:] = [server for server in writers if server.active_handler_count() > 0]


def surface_busy(current: _Hook | None, retired: list[_Hook]) -> bool:
    """Include retired handlers until their existing cleanup owner settles them."""
    return any(
        server.active_handler_count() > 0
        for server in ([current] if current is not None else []) + retired
    )


def drain_observations(
    *, sweep_active: bool, hooks: list[_Hook], classifiers: list[_Classifier],
    writers: list[_Writer], tracking_write: ModuleType, allocation_linker: AllocationLinkRuntime,
    worktrees_runtime: WorktreesRuntime, control_server: ControlServer,
    repositories_runtime: RepositoryMapRuntime, receipts: ReceiptControl, lifecycle_count: int,
    segment_cache: ModuleType, worktree_status_runtime: WorktreeStatusRuntime,
) -> dict[str, int]:
    """Sample existing request and retained-custody counts for drain diagnostics."""
    from . import repository_map

    result = {
        "sweep": int(sweep_active),
        "hook": sum(server.active_handler_count() for server in hooks),
        "classify": sum(server.active_handler_count() for server in classifiers),
        "tracking-write": sum(server.active_handler_count() for server in writers),
        "tracking-write-compute": int(tracking_write.has_inflight_write()),
        "allocation-link": allocation_linker.active_count(),
        "worktrees-feed": worktrees_runtime.feed.active_count(),
        **worktrees_runtime.agenda_counts(control_server),
        **worktrees_runtime.observation_counts(control_server),
        "worktrees-read": control_server.current_read_count(),
        "repos-map-read": control_server.current_read_count(repository_map.READ_ACTION),
        "configuration-owner-read": control_server.current_read_count("configuration-owner-read"),
        "repos-map-producer": int(repositories_runtime.health()["repositories"]["refreshing"]),
        "prompt-receipt": receipts.active_count(),
        "lifecycle": lifecycle_count,
    }
    result.update(segment_cache.custody_observations(worktree_status_runtime))
    return result


def retire_monitor(
    *, close_admission: Callable[[], object], repositories_runtime: RepositoryMapRuntime,
    worktrees_runtime: WorktreesRuntime, close_request_surfaces: Callable[[], object],
    control_server: ControlServer, receipts: ReceiptControl,
    worktree_status_runtime: WorktreeStatusRuntime, managed_mux_runtime: ManagedMuxRuntime,
    lock: Path, original_error: BaseException | None,
    allocation_linker: AllocationLinkRuntime | None = None,
    finish_observation_custody: Callable[[], object] | None = None,
    retirement_guard: _ReleaseGuard | None = None,
) -> None:
    error = original_error
    traceback = error.__traceback__ if error is not None else None
    pending: list[Callable[[], object]] = []
    reported: set[tuple[str, str]] = set()

    def attempt(operation: Callable[[], object], *, retry: bool = True) -> None:
        nonlocal error, traceback
        try:
            operation()
        except BaseException as exc:
            detail = (getattr(operation, "__qualname__", type(operation).__name__), str(exc))
            if detail not in reported:
                reported.add(detail)
                logging.getLogger(__name__).error("Monitor retirement cleanup pending at %s: %s", *detail)
            if error is None:
                error, traceback = exc, exc.__traceback__
            if retry:
                pending.append(operation)

    attempt(close_admission)
    attempt(repositories_runtime.shutdown)
    attempt(lambda: worktrees_runtime.feed.wait_closed(status_monitor_cutover._READ_TIMEOUT_S))
    attempt(lambda: worktrees_runtime.agendas.feed.wait_closed(
        status_monitor_cutover._READ_TIMEOUT_S,
    ))
    attempt(lambda: worktrees_runtime.observation_wait_closed(
        status_monitor_cutover._READ_TIMEOUT_S,
    ))
    attempt(close_request_surfaces)
    attempt(control_server.close)
    attempt(receipts.wait_idle)
    attempt(resident_push.reset)
    attempt(worktrees_runtime.shutdown)
    if allocation_linker is not None:
        attempt(allocation_linker.shutdown_and_wait, retry=False)
    # This domain method guarantees settlement before resurfacing its error.
    # Earlier failures must neither skip it nor replace the original failure.
    attempt(worktree_status_runtime.shutdown_and_wait, retry=False)
    if finish_observation_custody is not None:
        attempt(finish_observation_custody)
    attempt(managed_mux_runtime.shutdown_and_wait, retry=False)

    # Failed intake/authority cleanup is unknown, not a successful zero count.
    # Retry it only after every independent mandatory cleanup was attempted.
    while pending:
        retry, pending = pending, []
        for operation in retry:
            attempt(operation)
        if pending:
            try:
                time.sleep(0.01)
            except BaseException as exc:
                if error is None:
                    error, traceback = exc, exc.__traceback__

    def release_route() -> None:
        status_monitor_cutover.clear_route_if_owner(os.getpid())
        data = locks.read_lock(lock)
        if isinstance(data, dict) and data.get("pid") == os.getpid():
            locks.remove_lock(lock)

    try:
        release_route()
    except BaseException as exc:
        if error is None:
            error, traceback = exc, exc.__traceback__
    finally:
        if retirement_guard is not None:
            try:
                retirement_guard.release()
            except BaseException as exc:
                if error is None:
                    error, traceback = exc, exc.__traceback__
    if error is not None:
        raise error.with_traceback(traceback)
