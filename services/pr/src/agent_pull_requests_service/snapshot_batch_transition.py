"""Explicit snapshot metadata transitions under the existing service writer lease."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from types import ModuleType
from typing import TYPE_CHECKING, cast

from single_instance_lease import SingleInstance

from .local_config import LocalHostConfig

if TYPE_CHECKING:
    from agent_pull_requests.snapshot_batch_recovery import RecoveryRecord, RecoverySelection


def _require_selected(config: LocalHostConfig) -> None:
    if not config.github_snapshot_batches or config.filtered:
        raise ValueError("snapshot batch transition requires its selected local profile")


def adopt(config: LocalHostConfig) -> None:
    """Adopt v1 batch metadata without interpreting any unresolved outcome."""
    _require_selected(config)
    store = config.store()
    if not store.path.is_file() or store.path.is_symlink():
        raise ValueError("snapshot batch transition requires an existing owner volume")
    lease = SingleInstance(config.root, service="agent-pull-requests-watch")
    pid = os.getpid()
    deadline = time.monotonic() + config.operation_seconds

    def lifecycle() -> None:
        if not lease.held or os.getpid() != pid or time.monotonic() >= deadline:
            raise ValueError("snapshot batch reader exclusion unavailable")

    from agent_pull_requests.snapshot_batch_state import adopt as adopt_state
    with lease:
        lifecycle()
        adopt_state(
            store, lifecycle_checkpoint=lifecycle, checkpoint=lifecycle,
            expires_at=math.ceil(time.time() + config.operation_seconds),
        )


def _recovery_core() -> ModuleType:
    try:
        from agent_pull_requests import snapshot_batch_recovery

        if snapshot_batch_recovery.RECOVERY_PROTOCOL != "agent-pull-requests.snapshot-query-recovery/v1" or not all(
            callable(entry) for entry in (
                snapshot_batch_recovery.RecoverySelection,
                snapshot_batch_recovery.recover,
                snapshot_batch_recovery.read_recovery,
            )
        ):
            raise ValueError
    except (ImportError, AttributeError, ValueError):
        raise ValueError("executing snapshot query recovery core unsupported") from None
    return snapshot_batch_recovery


def recovery_contract() -> str:
    """Report support of the executing standalone adapter and its selected Core."""
    return str(_recovery_core().RECOVERY_PROTOCOL)


def _require_scope(config: LocalHostConfig, selection: RecoverySelection) -> None:
    from agent_pull_requests.snapshot_batch_state import scope

    _require_selected(config)
    if not any(
        (identity := binding.repository_identity) is not None
        and identity.provider == "github" and scope(identity) == selection.key
        for binding in config.observations
    ):
        raise ValueError("snapshot query recovery scope is not configured in this owner")


def recover(
    config: LocalHostConfig, selection: RecoverySelection, *,
    owner_checkpoint: Callable[[], None], expires_at: int,
) -> RecoveryRecord:
    """Compose the embedding owner's verified graceful drain with writer exclusion.

    The supervisor supplies a current checkpoint for its joined predecessor and
    exact selected volume. A free lease alone is not evidence of graceful drain.
    """
    receiver = _recovery_core()
    if not callable(owner_checkpoint) or not isinstance(selection, receiver.RecoverySelection):
        raise ValueError("explicit snapshot query recovery and owner checkpoint required")
    deadline = time.monotonic() + config.operation_seconds
    _require_scope(config, selection)
    store = config.store()
    lease = SingleInstance(config.root, service="agent-pull-requests-watch")

    def current_owner() -> None:
        owner_checkpoint()
        if time.monotonic() >= deadline:
            raise TimeoutError("snapshot query recovery lifecycle expired")

    def lifecycle() -> None:
        current_owner()
        lease.held_file_identity()

    store.check_current(expires_at, current_owner)
    if not store.path.is_file() or store.path.is_symlink():
        raise ValueError("snapshot query recovery requires an existing owner volume")
    lock = lease.lock_path.lstat()
    lease.acquire(expected_file_identity=(lock.st_dev, lock.st_ino))
    try:
        return cast("RecoveryRecord", receiver.recover(
            store, selection, lifecycle_checkpoint=lifecycle, expires_at=expires_at,
        ))
    finally:
        lease.release()


def read_recovery(
    config: LocalHostConfig, selection: RecoverySelection, *,
    expires_at: int, checkpoint: Callable[[], None] | None = None,
) -> RecoveryRecord | None:
    """Reconcile the original selection without requesting another transition."""
    receiver = _recovery_core()
    if not isinstance(selection, receiver.RecoverySelection):
        raise ValueError("typed snapshot query recovery selection required")
    _require_scope(config, selection)
    return cast("RecoveryRecord | None", receiver.read_recovery(
        config.store(), selection, expires_at=expires_at, checkpoint=checkpoint,
    ))
