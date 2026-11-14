"""Finite native observation cycle for an existing replay owner, not a poller."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from types import MappingProxyType

from .watch_contract import PRSnapshot
from .watch_provider import command_path, fetch_snapshot
from .watch_replay import ConsumerScope, ObservationTarget, ReplayEventTooLarge
from .watch_replay_store import ReplayStore
from single_instance_lease import OperationLockedError
from .repository_contract import RepositoryIdentity
from pathlib import Path
from .repository_native import (
    RepositoryNativeUnsupported, inventory as fetch_inventory,
    qualify as qualify_native,
)
from .repository_projection import RepositoryProjection, normalized, validate_identity

_MAX_READS = 4
_CHECK_INTERVAL = 0.1
_MAX_REPOSITORY_READS = 32


@dataclass(frozen=True)
class NativeObservationBinding:
    """Trusted deployment configuration; labels do not establish authorization."""

    account: str
    repo: str
    command: str
    prefix: tuple[str, ...] | None = None
    credential_command: tuple[str, ...] | None = None
    repository_identity: RepositoryIdentity | None = None
    inventory_config: str | None = None

    def __post_init__(self) -> None:
        ConsumerScope("observer", self.account, self.repo)
        command_path(self.command)
        if self.repository_identity is not None:
            if (
                type(self.repository_identity) is not RepositoryIdentity
                or self.repository_identity.key != self.account
                or self.repository_identity.repo != self.repo
                or self.credential_command is None
                or not isinstance(self.inventory_config, str)
                or not Path(self.inventory_config).is_absolute()
            ):
                raise ValueError("repository observation requires a full selected native read binding")
        elif self.inventory_config is not None:
            raise ValueError("inventory configuration requires repository observation selection")
        for argv in (self.prefix, self.credential_command):
            if argv is not None:
                if (
                    not isinstance(argv, tuple) or not argv
                    or any(not isinstance(item, str) or not item or "\0" in item for item in argv)
                ):
                    raise ValueError("native observation argv must be immutable")
                command_path(argv[0])


class ReplayObserver:
    """Share native reads only within the owner's configured account/repo scope.

    The embedding lifecycle serializes cycles and supplies current authority.
    Provider calls run outside replay transaction custody. Loss never cancels
    subscriptions or selects an ambient/alternate credential source.
    """

    def __init__(
        self, store: ReplayStore, *, bindings: tuple[NativeObservationBinding, ...],
    ) -> None:
        if not isinstance(bindings, tuple):
            raise ValueError("native observation bindings must be immutable")
        selected = {}
        for binding in bindings:
            if type(binding) is not NativeObservationBinding:
                raise ValueError("invalid native observation binding")
            key = (binding.account, binding.repo)
            if key in selected:
                raise ValueError("duplicate native observation scope")
            selected[key] = binding
        self.store = store
        self.bindings = MappingProxyType(selected)
        identities = tuple(b.repository_identity for b in bindings if b.repository_identity is not None)
        if identities and store.repository_filtered:
            from .filtered_projection import FilteredProjection
            from pr_filters import PROTOCOL
            self.repositories = FilteredProjection(store, identities, capabilities={
                identity.key: {"protocol": PROTOCOL, "fields": []} for identity in identities
            })
        else:
            self.repositories = RepositoryProjection(store, identities) if identities else None
        self._repository_cursor = 0
        self._query_cursors = {}
        self._cycle = threading.Lock()

    def cycle(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Run one bounded pass; caller owns scheduling, drain and renewal."""
        if not self._cycle.acquire(blocking=False):
            raise RuntimeError("native observation cycle already running")
        try:
            return self._observe(expires_at=expires_at, checkpoint=checkpoint)
        finally:
            self._cycle.release()

    def _disk_call(self, operation, *, expires_at, checkpoint):
        deadline = time.monotonic() + max(0, expires_at - self.store.clock())
        while True:
            self.store.check_current(expires_at, checkpoint)
            try:
                return operation()
            except OperationLockedError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(min(.01, max(0, deadline - time.monotonic())))

    def _observe(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None,
    ) -> dict:
        authority = {"expires_at": expires_at, "checkpoint": checkpoint}
        if self.store.repository_filtered:
            return self._observe_filtered(**authority)
        observed_at = int(self.store.clock())
        result = self.store.sweep_timeouts(**authority)
        if result["status"] == "uncertain":
            return result
        waiting = self.store.observation_targets(**authority)
        if self.repositories is None:
            return self._consume(waiting, **authority)
        inventories = {}
        qualified = set()
        configured = {
            (binding.account, binding.repo) for binding in self.bindings.values()
            if binding.repository_identity is not None
        }
        totals = {"observed": 0, "unavailable": 0, "unbound": 0}
        legacy = [target for target in waiting if (target.account, target.repo) not in configured]
        result = self._consume(legacy, **authority)
        if result["status"] != "observed":
            return result
        for name in totals:
            totals[name] += result[name]

        def check():
            self.store.check_current(expires_at, checkpoint)

        prepared = {}

        def capture(target, snapshot):
            key = (target.account, target.repo, target.number)
            try:
                prepared[key] = normalized(
                    snapshot, self.repositories.identities[key[0]], target.number,
                ) if snapshot is not None else None
            except (ValueError, TypeError):
                prepared[key] = None

        for binding in self.bindings.values():
            if binding.repository_identity is None:
                continue
            try:
                qualify_native(binding, checkpoint=check)
                qualified.add((binding.account, binding.repo))
            except RepositoryNativeUnsupported:
                raise
            except ValueError:
                check()
                inventories[binding.account] = {
                    "outcome": "unavailable", "numbers": [], "pages": 0,
                    "error": "qualification_unavailable",
                }
                continue
            current = [target for target in waiting if (
                target.account, target.repo
            ) == (binding.account, binding.repo)]
            result = self._consume(current, on_read=capture, **authority)
            if result["status"] != "observed":
                return result
            for name in totals:
                totals[name] += result[name]
        for binding in self.bindings.values():
            if (binding.account, binding.repo) not in qualified:
                continue
            try:
                inventories[binding.account] = fetch_inventory(
                    binding, checkpoint=check, _qualified=True,
                )
            except RepositoryNativeUnsupported:
                raise
            except ValueError:
                check()
                inventories[binding.account] = {
                    "outcome": "unavailable", "numbers": [], "pages": 0,
                    "error": "inventory_unavailable",
                }
        totals["unavailable"] += sum(
            scan["outcome"] == "unavailable" for scan in inventories.values()
        )
        repository_targets = self._disk_call(lambda: self.repositories.plans(inventories, **authority), **authority)
        seen = set(prepared)

        def publish():
            self._disk_call(lambda: self.repositories.apply_facts(
                inventories, prepared, observed_at=observed_at, **authority,
            ), **authority)
            prepared.clear()

        while len(prepared) > _MAX_REPOSITORY_READS:
            batch = {key: prepared.pop(key) for key in list(prepared)[:_MAX_REPOSITORY_READS]}
            self._disk_call(lambda: self.repositories.apply_facts(
                inventories, batch, observed_at=observed_at, **authority,
            ), **authority)
        publish()
        keys = sorted(key for key in repository_targets if key not in seen and key[:2] in qualified)
        if keys:
            start = self._repository_cursor % len(keys)
            selected = (keys[start:] + keys[:start])[:_MAX_REPOSITORY_READS]
            self._repository_cursor = (start + len(selected)) % len(keys)
            targets = [repository_targets[key] for key in selected]
            result = self._consume(targets, cache_keys=set(selected), on_read=capture, **authority)
            if result["status"] != "observed":
                return result
            for name in totals:
                totals[name] += result[name]
            publish()
        return {"status": "observed", **totals}

    def _observe_filtered(self, *, expires_at, checkpoint):
        from .filtered_native import population
        from .repository_native import RepositoryNativeUnavailable
        authority = {"expires_at": expires_at, "checkpoint": checkpoint}
        def check():
            self.store.check_current(expires_at, checkpoint)
        def demands_now():
            return self._disk_call(lambda: self.repositories.demands(**authority), **authority)
        demands = demands_now()
        totals = {"observed": 0, "unavailable": 0, "unbound": 0}
        keys = sorted(demands)
        if keys:
            start = self._repository_cursor % len(keys)
            keys = keys[start:] + keys[:start]
            self._repository_cursor = (start + 1) % len(keys)
        for key in keys:
            demand = demands[key]
            check()
            # Refresh interest before each new read; another consumer may have
            # withdrawn while the previous repository's owned read settled.
            current = demands_now().get(key)
            if current is None or current["registrations"] != demand["registrations"]:
                continue
            identity = self.repositories.identities[key]
            binding = self.bindings[(key, identity.repo)]
            observed_at = int(self.store.clock())
            class Withdrawn(Exception):
                pass
            def interested():
                check()
                current = demands_now().get(key)
                if current is None or not any(current["registrations"].get(registration) == revision
                                          for registration, revision in demand["registrations"].items()):
                    raise Withdrawn()
            def starting():
                check()
                current = demands_now().get(key)
                if current is None or current["registrations"] != demand["registrations"]:
                    raise Withdrawn()
            try:
                offset = self._query_cursors.get(key, 0)
                self._query_cursors[key] = (offset + 1) % 64
                scan = population(
                    binding, demand["filter"], root=self.store.root,
                    checkpoint=interested, start_checkpoint=starting, query_offset=offset,
                )
            except Withdrawn:
                continue
            except RepositoryNativeUnavailable:
                scan = {"outcome": "unavailable", "error": "population_unavailable", "pages": 0, "pulls": []}
            totals["unavailable"] += scan["outcome"] == "unavailable"
            result = self._disk_call(lambda: self.repositories.apply(
                key, demand, scan, observed_at=observed_at, **authority,
            ), **authority)
            totals["observed"] += result["observed"]
        return {"status": "observed", **totals}

    def _consume(
        self, targets, *, expires_at, checkpoint, cache_keys=frozenset(), on_read=None,
    ):
        authority = {"expires_at": expires_at, "checkpoint": checkpoint}
        targets = iter(targets)
        observed = unbound = 0
        unavailable = 0
        pending = {}
        exhausted = False
        stopped = threading.Event()
        # Joining the executor retains cycle/writer custody even after authority
        # loss; cancelling a Future is not proof that its native read settled.
        readers = ThreadPoolExecutor(max_workers=_MAX_READS)
        try:
            while pending or not exhausted:
                self.store.check_current(expires_at, checkpoint)
                result = self._disk_call(lambda: self.store.sweep_timeouts(**authority), **authority) if (
                    pending
                ) else self.store.sweep_timeouts(**authority)
                if result["status"] == "uncertain":
                    return result
                while not exhausted and len(pending) < _MAX_READS:
                    self.store.check_current(expires_at, checkpoint)
                    target = next(targets, None)
                    if target is None:
                        exhausted = True
                        break
                    binding = self.bindings.get((target.account, target.repo))
                    if binding is None:
                        unbound += 1
                        continue
                    if (target.account, target.repo, target.number) not in cache_keys:
                        target = self._disk_call(lambda: self.store.waiting_target(target, **authority), **authority)
                    if target is not None:
                        future = readers.submit(
                            self._read, target, binding, stopped=stopped, **authority,
                        )
                        pending[future] = target
                if not pending:
                    continue
                done, _ = wait(pending, timeout=_CHECK_INTERVAL, return_when=FIRST_COMPLETED)
                for future in done:
                    target = pending.pop(future)
                    self.store.check_current(expires_at, checkpoint)
                    snapshot = future.result()
                    if snapshot is None:
                        unavailable += 1
                    else:
                        try:
                            result = self._disk_call(lambda: self.store.observe(target, snapshot, **authority),
                                                     **authority) if (
                                target.registration_ids or target.retention_epoch is not None
                            ) else {"status": "observed"}
                        except ReplayEventTooLarge:
                            unavailable += 1
                        else:
                            if result["status"] == "uncertain":
                                return result
                            observed += 1
                    if on_read is not None:
                        on_read(target, snapshot)
                    result = self._disk_call(lambda: self.store.sweep_timeouts(**authority), **authority)
                    if result["status"] == "uncertain":
                        return result
        finally:
            stopped.set()
            readers.shutdown(wait=True, cancel_futures=True)
        self.store.check_current(expires_at, checkpoint)
        return {
            "status": "observed", "observed": observed,
            "unavailable": unavailable, "unbound": unbound,
        }

    def _read(
        self, target: ObservationTarget, binding: NativeObservationBinding, *,
        stopped: threading.Event, expires_at: int, checkpoint: Callable[[], None] | None,
    ) -> PRSnapshot | None:
        if stopped.is_set():
            return None
        self.store.check_current(expires_at, checkpoint)
        try:
            snapshot = fetch_snapshot(
                target.repo, target.number, binding.command,
                prefix=list(binding.prefix) if binding.prefix is not None else None,
                credential_command=(
                    list(binding.credential_command)
                    if binding.credential_command is not None else None
                ),
                **({"config_file": binding.inventory_config} if binding.inventory_config else {}),
            )
            if snapshot is not None and binding.repository_identity is not None:
                validate_identity(snapshot, binding.repository_identity, target.number)
            return snapshot
        except ValueError:
            return None
