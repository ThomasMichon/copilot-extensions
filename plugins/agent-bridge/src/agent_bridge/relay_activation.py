"""Native routing authority for one frontend's relay activation."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import time
from collections.abc import Iterator

from fastapi import FastAPI
from zdd import routing
from zdd.routing import Endpoint


class RelayOwnershipLost(RuntimeError):
    """Prepared relay sources no longer belong to the serving frontend."""


@dataclass(frozen=True)
class RelayOwner:
    root: Path
    endpoint: Endpoint

    def verify(self, app: FastAPI) -> None:
        table = routing.read_table(self.root, strict=True) or {}
        server = getattr(app.state, "uvicorn_server", None)
        if (
            table.get("active") != self.endpoint.to_dict()
            or getattr(app.state, "shutting_down", False)
            or getattr(server, "should_exit", False)
        ):
            raise RelayOwnershipLost("relay frontend no longer owns its serving route")

    @contextmanager
    def activation(self, app: FastAPI) -> Iterator[None]:
        with routing.routing_lock(self.root, deadline=time.monotonic() + 2.0):
            self.verify(app)
            yield


def observe_owner(app: FastAPI) -> RelayOwner | None:
    if not any(getattr(app.state, key, False) for key in (
        "publish_on_ready", "passive", "independent_runtime_start",
    )):
        return None
    from .config import config_dir

    root = Path(config_dir())
    table = routing.read_table(root, strict=True) or {}
    raw = table.get("active")
    endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
    if (
        endpoint is None or endpoint.pid != os.getpid()
        or endpoint.port != getattr(app.state, "bound_port", None)
        or not endpoint.process_start_time
    ):
        raise RelayOwnershipLost("relay frontend has no current serving route")
    owner = RelayOwner(root, endpoint)
    owner.verify(app)
    return owner
