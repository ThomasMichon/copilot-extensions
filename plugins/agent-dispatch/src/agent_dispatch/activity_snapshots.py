"""Capture coordinator admission fences before shared liveness observations."""

from __future__ import annotations

from typing import Any

from .client import DispatchError


def capture(client: Any, reservations: list[dict]) -> list[tuple[dict, dict]]:
    snapshots = []
    for reservation in reservations:
        try:
            snapshots.append((reservation, client.get(reservation["task_id"])))
        except DispatchError:
            continue
    return snapshots
