"""Batch only the existing observer's already-selected per-PR work."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import threading
import time
from typing import TYPE_CHECKING

from .filtered_state import Authority, Checkpoint, Document
from .repository_contract import RepositoryIdentity
from .snapshot_batch_state import BatchAdmission, scope
from .watch_contract import PRSnapshot
from .watch_replay import ObservationTarget, ReplayEventTooLarge

if TYPE_CHECKING:
    from .watch_observer import NativeObservationBinding, ReplayObserver, TargetKey

Read = tuple[ObservationTarget, PRSnapshot | None]
Group = tuple[ObservationTarget, ...]


def allowed(
    owner: ReplayObserver, binding: NativeObservationBinding, *, expires_at: int,
    checkpoint: Checkpoint | None,
) -> bool:
    identity = binding.repository_identity
    return bool(
        not owner.store.github_snapshot_batches or identity is None or identity.provider != "github"
        or BatchAdmission(owner.store).allowed(
            scope(identity), query=False, expires_at=expires_at, checkpoint=checkpoint,
        )
    )


def _read(
    owner: ReplayObserver, group: Group, binding: NativeObservationBinding, *,
    stopped: threading.Event, expires_at: int, checkpoint: Checkpoint | None, deadline: float,
) -> list[Read]:
    empty: list[Read] = [(target, None) for target in group]
    if stopped.is_set():
        return empty
    if binding.snapshot_batch is None:
        target = group[0]
        return [(target, owner._read(
            target, binding, stopped=stopped, expires_at=expires_at, checkpoint=checkpoint,
        ))]
    identity = binding.repository_identity
    assert identity is not None
    admission, key = BatchAdmission(owner.store), scope(identity)
    numbers = tuple(target.number for target in group if 1 <= target.number <= 2_147_483_647)
    if not numbers:
        return empty
    operation: str | None = None

    def admitted() -> None:
        nonlocal operation
        operation = admission.begin(key, expires_at=expires_at, checkpoint=checkpoint)
        if operation is None:
            raise ValueError("provider query admission withheld")

    def check() -> None:
        owner.store.check_current(expires_at, checkpoint)
        if stopped.is_set():
            raise ValueError("snapshot batch cycle stopped")

    try:
        from .snapshot_batch_native import acquire
        snapshots, backoff, resolved = acquire(
            binding, numbers, checkpoint=check, deadline=deadline, on_admitted=admitted,
        )
    except ValueError:
        # No trustworthy result means no proof of the provider's throttle state.
        # Keep the durable pending marker; time, restart and another PR cannot clear it.
        check()
        return empty
    if operation is None:
        raise ValueError("native query omitted its durable admission")
    if resolved:
        admission.finish(key, operation, backoff, expires_at=expires_at, checkpoint=checkpoint)
    return [(target, snapshots.get(target.number)) for target in group]


def consume(
    owner: ReplayObserver, targets: Iterable[ObservationTarget], *, expires_at: int,
    checkpoint: Checkpoint | None, cache_keys: Collection[TargetKey],
    on_read: Callable[[ObservationTarget, PRSnapshot | None], None] | None,
) -> Document:
    authority: Authority = {"expires_at": expires_at, "checkpoint": checkpoint}
    deadline = time.monotonic() + max(0, expires_at - owner.store.clock())
    groups: list[tuple[NativeObservationBinding, list[ObservationTarget]]] = []
    unbound = observed = unavailable = 0
    for target in targets:
        binding = owner.bindings.get((target.account, target.repo))
        if binding is None:
            unbound += 1
            continue
        width = 10 if binding.snapshot_batch is not None else 1
        if groups and groups[-1][0] == binding and len(groups[-1][1]) < width:
            groups[-1][1].append(target)
        else:
            groups.append((binding, [target]))
    pending: dict[Future[list[Read]], str | None] = {}
    stopped = threading.Event()
    concurrency = owner.native_read_concurrency or 4
    readers = ThreadPoolExecutor(max_workers=concurrency)
    try:
        while groups or pending:
            owner.store.check_current(expires_at, checkpoint)
            busy = {key for key in pending.values() if key is not None}
            index = 0
            while index < len(groups) and len(pending) < concurrency:
                binding, selected = groups[index]
                identity: RepositoryIdentity | None = binding.repository_identity
                key = scope(identity) if identity is not None and identity.provider == "github" else None
                if key is not None and key in busy:
                    index += 1
                    continue
                groups.pop(index)
                current: list[ObservationTarget] = []
                for target in selected:
                    refreshed: ObservationTarget | None = target
                    if (target.account, target.repo, target.number) not in cache_keys:
                        waiting = target
                        refreshed = owner._disk_call(
                            lambda: owner.store.waiting_target(waiting, **authority), **authority,
                        )
                    if refreshed is not None:
                        current.append(refreshed)
                if current:
                    future = readers.submit(
                        _read, owner, tuple(current), binding, stopped=stopped,
                        deadline=deadline, **authority,
                    )
                    pending[future] = key
                    if key is not None:
                        busy.add(key)
            if pending:
                done, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future)
                    for target, snapshot in future.result():
                        owner.store.check_current(expires_at, checkpoint)
                        if snapshot is None:
                            unavailable += 1
                        else:
                            try:
                                result = owner._disk_call(
                                    lambda: owner.store.observe(target, snapshot, **authority), **authority,
                                ) if target.registration_ids or target.retention_epoch is not None else {
                                    "status": "observed",
                                }
                            except ReplayEventTooLarge:
                                unavailable += 1
                            else:
                                if result["status"] == "uncertain":
                                    return result
                                observed += 1
                        if on_read is not None:
                            on_read(target, snapshot)
            result = owner._disk_call(lambda: owner.store.sweep_timeouts(**authority), **authority)
            if result["status"] == "uncertain":
                return result
    finally:
        stopped.set()
        readers.shutdown(wait=True, cancel_futures=True)
    owner.store.check_current(expires_at, checkpoint)
    return {"status": "observed", "observed": observed, "unavailable": unavailable, "unbound": unbound}
