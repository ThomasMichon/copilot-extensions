"""Explicit additive metadata adoption under the service's existing writer lease."""

from __future__ import annotations

import math
import os
import time

from single_instance_lease import SingleInstance

from .local_config import LocalHostConfig


def adopt(config: LocalHostConfig) -> None:
    if not config.github_snapshot_batches or config.filtered:
        raise ValueError("snapshot batch transition requires its selected local profile")
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
