"""Disk-backed replay transactions, separate from the native callback owner."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from single_instance_lease import OperationLock

from .watch_contract import PRSnapshot
from .watch_replay import ConsumerScope, ObservationTarget, WatchReplay
from .watch_storage import _atomic_write_json

_T = TypeVar("_T")


class ReplayStoreInvalid(ValueError):
    """Previously provisioned replay state cannot be restored."""


class ReplayCallerExpired(PermissionError):
    """Caller authority expired before a transaction could commit."""


def json_object(raw: bytes) -> dict:
    def pairs(items: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


class ReplayStore:
    """Hold existing operation custody across each complete load/use/commit.

    No cached core outlives its transaction. A cancelled network request may
    still commit in its worker thread; the kernel lock stays held until that
    transaction finishes. Reconcile its original identity, never assume absence.
    This is not a daemon lease or placement/update authority.
    """

    def __init__(
        self, root: Path, namespace: str, *, clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root)
        if not self.root.is_absolute() or self.root.resolve() != self.root:
            raise ValueError("replay store requires an absolute canonical root")
        self.namespace = namespace
        self.clock = clock
        self.path = self.root / "watch-replay.json"
        # Validate the namespace without reading or provisioning any state.
        WatchReplay(namespace, state=None, commit=lambda _state: None, clock=clock)

    def _lock(self) -> OperationLock:
        return OperationLock(self.root / "watch-replay.lock")

    def provision(self) -> None:
        """Explicit first creation; never replace existing or corrupt state."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock():
            if self.path.exists() or self.path.is_symlink():
                raise FileExistsError("replay store already exists")
            core = WatchReplay(
                self.namespace, state=None, commit=lambda _state: None, clock=self.clock,
            )
            _atomic_write_json(self.path, core.snapshot())

    def check_current(self, expires_at: int, checkpoint: Callable[[], None] | None) -> None:
        """Check the embedding owner's current authority, including between reads."""
        if type(expires_at) is not int or expires_at <= self.clock():
            raise ReplayCallerExpired("caller context expired")
        if checkpoint is not None:
            checkpoint()

    def _transaction(
        self, operation: Callable[[WatchReplay], _T], *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> _T:
        if checkpoint is not None and not callable(checkpoint):
            raise ValueError("invalid request checkpoint")
        self.check_current(expires_at, checkpoint)
        with self._lock() as lock:
            self.check_current(expires_at, checkpoint)

            def commit(state: dict) -> None:
                lock.assert_owned()
                self.check_current(expires_at, checkpoint)
                _atomic_write_json(self.path, state)

            if self.path.is_symlink():
                raise ReplayStoreInvalid("linked replay state")
            raw = self.path.read_bytes()  # Missing state is not first provisioning.
            try:
                core = WatchReplay(
                    self.namespace, state=json_object(raw), commit=commit, clock=self.clock,
                )
            except (ValueError, UnicodeError, RecursionError):
                raise ReplayStoreInvalid("invalid persisted replay state") from None
            self.check_current(expires_at, checkpoint)
            result = operation(core)
            self.check_current(expires_at, checkpoint)
            return result

    def observation_targets(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> tuple[ObservationTarget, ...]:
        return self._transaction(
            lambda core: core.observation_targets(), expires_at=expires_at, checkpoint=checkpoint,
        )

    def observe(
        self, target: ObservationTarget, snapshot: PRSnapshot, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Trusted owner-only input, never a serving operation."""
        return self._transaction(
            lambda core: core.observe(
                target.account, target.repo, target.number, snapshot,
                registration_ids=target.registration_ids,
            ), expires_at=expires_at, checkpoint=checkpoint,
        )

    def waiting_target(
        self, target: ObservationTarget, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> ObservationTarget | None:
        return self._transaction(
            lambda core: core.waiting_target(target),
            expires_at=expires_at, checkpoint=checkpoint,
        )

    def sweep_timeouts(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        return self._transaction(
            lambda core: core.sweep_timeouts(), expires_at=expires_at, checkpoint=checkpoint,
        )

    def execute(
        self, scope: ConsumerScope, operation: str, payload: dict, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        def dispatch(core: WatchReplay) -> dict:
            if operation == "pending":
                swept = core.sweep_timeouts()
                if swept["status"] == "uncertain":
                    result = swept
                else:
                    result = core.pending(scope, **payload)
            elif operation == "register":
                result = core.register(scope, **payload)
            elif operation == "inspect":
                result = core.inspect(scope, **payload)
            elif operation == "cancel":
                result = core.cancel(scope, **payload)
            elif operation == "acknowledge":
                result = core.acknowledge(scope, **payload)
            elif operation == "capabilities":
                from .watch_replay import MAX_BATCH, MAX_BATCH_BYTES, RETENTION_SECONDS

                result = {
                    "protocol": "agent-pull-requests.watch/v1",
                    "operations": [
                        "capabilities", "register", "inspect", "pending", "acknowledge", "cancel",
                    ],
                    "max_batch": MAX_BATCH, "max_batch_bytes": MAX_BATCH_BYTES,
                    "receipt_retention_seconds": RETENTION_SECONDS,
                }
            else:
                raise ValueError("unsupported replay operation")
            return result

        return self._transaction(dispatch, expires_at=expires_at, checkpoint=checkpoint)
