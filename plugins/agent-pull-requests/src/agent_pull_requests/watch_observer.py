"""Finite native observation cycle for an existing replay owner, not a poller."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType

from .watch_provider import command_path, fetch_snapshot
from .watch_replay import ConsumerScope, ReplayEventTooLarge
from .watch_replay_store import ReplayStore


@dataclass(frozen=True)
class NativeObservationBinding:
    """Trusted deployment configuration; labels do not establish authorization."""

    account: str
    repo: str
    command: str
    prefix: tuple[str, ...] | None = None
    credential_command: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        ConsumerScope("observer", self.account, self.repo)
        command_path(self.command)
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

    def _observe(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None,
    ) -> dict:
        authority = {"expires_at": expires_at, "checkpoint": checkpoint}
        result = self.store.sweep_timeouts(**authority)
        if result["status"] == "uncertain":
            return result
        targets = self.store.observation_targets(**authority)
        observed = unavailable = unbound = 0
        for target in targets:
            self.store.check_current(expires_at, checkpoint)
            binding = self.bindings.get((target.account, target.repo))
            if binding is None:
                unbound += 1
                continue
            target = self.store.waiting_target(target, **authority)
            if target is None:
                continue
            try:
                snapshot = fetch_snapshot(
                    target.repo, target.number, binding.command,
                    prefix=list(binding.prefix) if binding.prefix is not None else None,
                    credential_command=(
                        list(binding.credential_command)
                        if binding.credential_command is not None else None
                    ),
                )
            except ValueError:
                unavailable += 1
            else:
                try:
                    result = self.store.observe(target, snapshot, **authority)
                except ReplayEventTooLarge:
                    unavailable += 1
                else:
                    if result["status"] == "uncertain":
                        return result
                    observed += 1
            result = self.store.sweep_timeouts(**authority)
            if result["status"] == "uncertain":
                return result
        self.store.check_current(expires_at, checkpoint)
        return {
            "status": "observed", "observed": observed,
            "unavailable": unavailable, "unbound": unbound,
        }
