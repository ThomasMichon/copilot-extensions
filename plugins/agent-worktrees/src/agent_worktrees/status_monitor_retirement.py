"""Mandatory, error-preserving retirement of the monitor's owned surfaces."""

from __future__ import annotations

import os
import time

from . import locks, resident_push, status_monitor_cutover


def prune_retired_surfaces(hooks, classifiers, writers, has_inflight_write) -> None:
    """Forget only handler-free surfaces while the caller holds its state lock."""
    hooks[:] = [server for server in hooks if server.active_handler_count() > 0]
    classifiers[:] = [server for server in classifiers if server.active_handler_count() > 0]
    if not has_inflight_write():
        writers[:] = [server for server in writers if server.active_handler_count() > 0]


def retire_monitor(
    *, close_admission, repositories_runtime, worktrees_runtime,
    close_request_surfaces, control_server, receipts, worktree_status_runtime,
    managed_mux_runtime, lock, original_error, allocation_linker=None,
    finish_observation_custody=None,
) -> None:
    error = original_error
    traceback = error.__traceback__ if error is not None else None
    pending = []

    def attempt(operation, *, retry=True):
        nonlocal error, traceback
        try:
            operation()
        except BaseException as exc:
            if error is None:
                error, traceback = exc, exc.__traceback__
            if retry:
                pending.append(operation)

    attempt(close_admission)
    attempt(repositories_runtime.shutdown)
    attempt(lambda: worktrees_runtime.feed.wait_closed(status_monitor_cutover._READ_TIMEOUT_S))
    attempt(lambda: worktrees_runtime.agendas.feed.wait_closed(status_monitor_cutover._READ_TIMEOUT_S))
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

    def release_route():
        status_monitor_cutover.clear_route_if_owner(os.getpid())
        data = locks.read_lock(lock)
        if isinstance(data, dict) and data.get("pid") == os.getpid():
            locks.remove_lock(lock)

    try:
        release_route()
    except BaseException as exc:
        if error is None:
            error, traceback = exc, exc.__traceback__
    if error is not None:
        raise error.with_traceback(traceback)
