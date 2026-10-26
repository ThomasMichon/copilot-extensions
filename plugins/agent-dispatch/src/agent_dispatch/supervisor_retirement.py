"""Final local-work retirement inside the supervisor's existing admission."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from concurrent.futures import wait

from .supervisor_registration import SELF_UPDATE_EXIT_CODE

log = logging.getLogger("agent-dispatch.supervisor-daemon")


def _owned_pools(daemon):
    pools = set(daemon._retired_runtime_executors) | set(daemon._retired_bounded_executors)
    for field in (
        "_receipt_executor", "_read_executor", "_publication_executor",
        "_provider_executor", "_inventory_executor", "_progress_executor",
    ):
        pool = getattr(daemon, field)
        if pool is not None:
            pools.add(pool)
    if daemon._owns_runtime_executor and daemon._runtime_executor is not None:
        pools.add(daemon._runtime_executor)
    return pools


def _tracked_work(daemon):
    pending = (
        daemon._shutdown_waited_futures
        | daemon._managed_runtime_inflight
        | daemon._probe_inflight
    )
    if daemon._managed_cleanup_future is not None:
        pending = pending | {daemon._managed_cleanup_future}
    return pending


def _retire_owned_workers(pools, deadline):
    retired = True
    for pool in pools:
        pool.shutdown(wait=False, cancel_futures=True)
        # Only owned ThreadPoolExecutor workers establish native retirement;
        # a completed Future can still be executing callbacks on its worker.
        workers = getattr(pool, "_threads", None)
        if workers is None:
            retired = False
            continue
        for worker in tuple(workers):
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
            retired = not worker.is_alive() and retired
    return retired


def _settle_local_work(daemon, interrupted=None, *, pools=None):
    pools = set() if pools is None else pools
    retry = False
    while True:
        try:
            if retry:
                time.sleep(0.05)
            pools.update(_owned_pools(daemon))
            daemon.shutdown()
            pools.update(_owned_pools(daemon))
            pending = _tracked_work(daemon)
            if any(not future.done() for future in pending):
                if not retry:
                    log.warning("supervisor local work retiring; daemon admission retained")
                wait(pending, timeout=0.05)
            else:
                for pool in pools:
                    pool.shutdown(wait=True, cancel_futures=True)
                return interrupted
        except BaseException as exc:
            if interrupted is None:
                interrupted = exc
                log.warning("supervisor retirement interrupted; daemon admission retained")
        retry = True


def finish_serving(daemon, *, single_instance: bool) -> None:
    interrupted = sys.exc_info()[1]
    pools = set()
    if daemon.self_update_triggered:
        from .supervisor_daemon import _SHUTDOWN_GRACE_SECONDS

        deadline = time.monotonic() + _SHUTDOWN_GRACE_SECONDS
        pools.update(_owned_pools(daemon))
        drained = False
        try:
            bounded_drained = daemon.shutdown()
            pools.update(_owned_pools(daemon))
            workers_retired = _retire_owned_workers(
                pools, deadline if bounded_drained else time.monotonic(),
            )
            drained = workers_retired and all(future.done() for future in _tracked_work(daemon))
        except BaseException as exc:
            if interrupted is None:
                interrupted = exc
    else:
        interrupted = _settle_local_work(daemon, interrupted, pools=pools)
        drained = True
    try:
        if daemon._client_factory is not None:
            close = getattr(daemon.client, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
    except BaseException as exc:
        if interrupted is None:
            interrupted = exc
    try:
        if single_instance:
            daemon.release_singleton()
    except BaseException as exc:
        if interrupted is None:
            interrupted = exc
    if daemon.self_update_triggered and not drained:
        log.warning(
            "supervisor self-update: local worker outlived shutdown "
            "grace; forcing exit to finish the committed handoff",
        )
        try:
            os._exit(SELF_UPDATE_EXIT_CODE)
        except BaseException as exc:
            if interrupted is None:
                interrupted = exc
        # A returned/refused force-exit is not actual old-process termination.
        interrupted = _settle_local_work(daemon, interrupted, pools=pools)
    if interrupted is not None:
        raise interrupted
