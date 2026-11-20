"""Exact creator-claim reconciliation through the coordinator owner."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import httpx


class HandoffClaimClientMixin:
    """Reconcile bookkeeping without replaying task abandonment."""

    if TYPE_CHECKING:
        _http: httpx.Client
        _unwrap: Callable[[httpx.Response], Any]
        _control_headers: Callable[[], dict[str, str]]

    def reconcile_handoff_claim(
        self, task_id: str, *, binding: dict[str, Any] | None = None, owner_ref: str | None = None,
    ) -> dict[str, Any]:
        """Bind or retry exact creator bookkeeping without replaying task abandonment."""
        timeout = httpx.Timeout(self._http.timeout)
        if timeout.read is not None:
            timeout.read = max(timeout.read, 60.0)
        return cast(dict[str, Any], self._unwrap(self._http.post(
            f"/tasks/{task_id}/handoff-claim",
            json={"binding": binding, "owner_ref": owner_ref},
            headers=self._control_headers(),
            timeout=timeout,
        )))
