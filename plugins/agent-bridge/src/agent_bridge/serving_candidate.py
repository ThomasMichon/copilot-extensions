"""Correlate a passive worker with its launch, independently of its launcher."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from zdd import diagnostics, process_exit, routing

from .serving_reconcile import ServingReconcileError, compare_serving_version

if TYPE_CHECKING:
    from .client import BridgeClient


def receipt_path(root: Path, launch_id: str) -> Path:
    if uuid.UUID(launch_id).hex != launch_id:
        raise ValueError("invalid serving launch identity")
    return root / f"serving-launch-{launch_id}.json"


def record_worker(root: Path, launch_id: str) -> None:
    """Only the actual CLI worker records its native birth, never the broker."""
    pid = os.getpid()
    start = diagnostics.process_start_time(pid)
    if not start:
        raise ServingReconcileError("successor native birth is unavailable")
    with receipt_path(root, launch_id).open("x", encoding="utf-8") as stream:
        json.dump({"launch_id": launch_id, "pid": pid, "process_start_time": start}, stream)
        stream.flush()
        os.fsync(stream.fileno())


class ServingCandidate:
    def __init__(
        self, root: Path, port: int, version: str,
        health: Callable[[], dict | None], make_client: Callable[[], BridgeClient],
        *, drain_timeout: float = 10.0,
        before_shutdown: Callable[[], None] | None = None,
    ) -> None:
        self.root, self.port, self.version = root, port, version
        self.launch_id = uuid.uuid4().hex
        self.launcher: object | None = None
        self.process: process_exit.ProcessExitReference | None = None
        self._health, self._make_client = health, make_client
        self._published = False
        self._published_endpoint: routing.Endpoint | None = None
        self._drain_timeout = drain_timeout
        self._before_shutdown = before_shutdown

    @property
    def pid(self) -> int | None:
        if self.process is None:
            path = receipt_path(self.root, self.launch_id)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                # The worker can still be importing or writing its receipt.
                return None
            if not isinstance(data, dict) or data.get("launch_id") != self.launch_id:
                raise ServingReconcileError("successor launch receipt is invalid")
            self.process = process_exit.open_process(
                data.get("pid"), data.get("process_start_time"),
            )
        return self.process.pid

    def verify_health(self, body: dict | None, *, ready: bool = True) -> None:
        pid = self.pid
        slot = body.get("slot") if isinstance(body, dict) else None
        active = (routing.read_table(self.root, strict=True) or {}).get("active")
        owns_route = isinstance(active, dict) and active.get("pid") == pid
        role = "active" if owns_route else "passive" if active else "unknown"
        if (
            pid is None or self.process is None or self.process.wait(0)
            or diagnostics.process_start_time(pid) != self.process.start_time
            or not isinstance(body, dict)
            or body.get("service") != "agent-bridge"
            or (ready and (
                body.get("status") != "ok" or body.get("ready") is not True
                or body.get("draining") is True
            ))
            or compare_serving_version(body.get("version"), self.version) != "current"
            or not isinstance(slot, dict) or type(slot.get("pid")) is not int
            or slot["pid"] != pid
            or slot.get("active") != active
            or slot.get("role") != role
            or (owns_route and (
                active.get("port") != self.port
                or active.get("process_start_time") != self.process.start_time
            ))
        ):
            raise ServingReconcileError("successor health does not identify the launched worker")

    def poll(self) -> int | None:
        if self.pid is None or self.process is None:
            return None
        return 0 if self.process.wait(0) else None

    @property
    def process_start_time(self) -> str | None:
        return self.process.start_time if self.process is not None else None

    def mark_published(self, endpoint: routing.Endpoint) -> None:
        self._published = True
        self._published_endpoint = endpoint
        if (
            endpoint.pid != self.pid or endpoint.port != self.port
            or endpoint.process_start_time != self.process_start_time
        ):
            raise ServingReconcileError("published successor differs from the launched worker")

    def terminate(self) -> None:
        if self.poll() == 0:
            return
        initial_health = self._health()
        self.verify_health(initial_health, ready=False)
        # No broker/tree signal: graceful control is admitted only after the
        # endpoint proves this launch's retained native worker identity.
        client = self._make_client()
        if not self._published:
            client.shutdown()
            return
        opened_gate = isinstance(initial_health, dict) and initial_health.get("draining") is False
        try:
            drained = client.drain(timeout=self._drain_timeout, poll=1.0, force=False)
            if (
                not isinstance(drained, dict) or drained.get("drained") is not True
                or drained.get("clean") is not True or drained.get("forced") is not False
            ):
                raise ServingReconcileError(
                    "published successor did not drain; worker and reconciliation evidence retained"
                )
            health = self._health()
            self.verify_health(health, ready=False)
            if not isinstance(health, dict) or health.get("draining") is not True:
                raise ServingReconcileError("published successor drain gate changed; worker retained")
            if self._before_shutdown is not None:
                self._before_shutdown()
            client.shutdown()
        except Exception as failure:
            if opened_gate:
                try:
                    self._restore_admission(client)
                except Exception as compensation:
                    raise ServingReconcileError(
                        f"successor cleanup failed ({type(failure).__name__}); "
                        f"admission compensation failed ({type(compensation).__name__}); "
                        "worker and reconciliation evidence retained"
                    ) from failure
            raise

    def _verify_active_publication(self, body: dict | None) -> dict:
        self.verify_health(body, ready=False)
        active = (routing.read_table(self.root, strict=True) or {}).get("active")
        if (
            self._published_endpoint is None
            or active != self._published_endpoint.to_dict()
            or not isinstance(body, dict)
            or body.get("slot", {}).get("role") != "active"
            or body["slot"].get("active") != active
        ):
            raise ServingReconcileError("successor no longer owns the exact active publication")
        return body

    def _restore_admission(self, client: BridgeClient) -> None:
        health = self._verify_active_publication(self._health())
        if health.get("draining") is False:
            return
        if health.get("draining") is not True:
            raise ServingReconcileError("successor admission state is unknown")
        response = client.undrain()
        if not isinstance(response, dict) or response.get("draining") is not False:
            raise ServingReconcileError("successor admission compensation was not acknowledged")
        health = self._verify_active_publication(self._health())
        if health.get("draining") is not False:
            raise ServingReconcileError("successor admission remained closed after compensation")

    def close(self, *, completed: bool) -> None:
        if self.process is not None:
            exited = self.process.wait(0)
            self.process.close()
            if completed or exited:
                receipt_path(self.root, self.launch_id).unlink(missing_ok=True)
