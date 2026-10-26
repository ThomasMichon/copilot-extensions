"""Finite, explicitly selected Docker observations; no provisioning or admission."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

from boundary import Experiment, inspect_static_boundary
from mounts import VolatileMounts, inspect_volatile_mounts
from network import NetworkIdentity, inspect_inventory, selected_containers
from process import run as _run
from storage import Resources, StorageMonitor, Usage, VolumeIdentity


class ReadError(RuntimeError):
    """A finite observation failed; it grants no lifecycle or retry authority."""


class DockerReader:
    """Use a caller-selected Docker CLI/context and a full physical container ID."""

    def __init__(
        self, docker: Path, *, run: Callable[..., subprocess.CompletedProcess[str]] = _run,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not docker.is_absolute():
            raise ValueError("Docker executable must be selected by absolute path")
        self.docker = docker
        self.run = run
        self.clock = clock

    def _json(self, arguments: list[str], deadline: float) -> object:
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise ReadError("Docker observation deadline exhausted")
        try:
            result = self.run([os.fspath(self.docker), *arguments], timeout=min(10, remaining))
        except (OSError, subprocess.SubprocessError, RuntimeError, UnicodeError) as error:
            raise ReadError(f"Docker observation unavailable ({type(error).__name__})") from error
        if result.returncode != 0:
            raise ReadError(f"Docker observation exited with status {result.returncode}")
        try:
            value = json.loads(result.stdout)
        except (ValueError, RecursionError) as error:
            raise ReadError("Docker observation is not bounded valid JSON") from error
        self._check_deadline(deadline)
        return value

    def _check_deadline(self, deadline: float) -> float:
        observed = self.clock()
        if observed >= deadline:
            raise ReadError("Docker observation deadline exhausted")
        return observed

    def inspect(
        self, experiment: Experiment, container_id: str, *, deadline: float | None = None,
    ) -> Resources:
        if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
            raise ValueError("a full physical container ID is required")
        end = self.clock() + 30 if deadline is None else deadline
        rows = self._json(["container", "inspect", container_id], end)
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ReadError("container inspection inventory is incomplete")
        container = rows[0]
        if container.get("Id") != container_id:
            raise ReadError("container identity changed")
        static = inspect_static_boundary(experiment, container)
        if static.violations:
            raise ReadError("; ".join(static.violations))
        names = tuple(experiment.volumes.values())
        rows = self._json(["volume", "inspect", *names], end)
        if not isinstance(rows, list) or len(rows) != len(names):
            raise ReadError("volume inspection inventory is incomplete")
        volumes: dict[str, VolumeIdentity] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ReadError("volume inspection is malformed")
            name = row.get("Name")
            created, mountpoint = row.get("CreatedAt"), row.get("Mountpoint")
            if (
                name not in names or name in volumes
                or row.get("Driver") != "local" or row.get("Scope") != "local"
                or "Options" not in row or row["Options"] not in (None, {})
                or not isinstance(created, str) or not created
                or not isinstance(mountpoint, str) or not mountpoint.startswith("/")
            ):
                raise ReadError("ordinary local volume identity/posture is unavailable")
            volumes[name] = VolumeIdentity(name, created, mountpoint)
        for mount in container["Mounts"]:
            if mount.get("Source") != volumes[mount["Name"]].mountpoint:
                raise ReadError("effective mount does not expose the full volume root")
        self._check_deadline(end)
        return Resources(container_id, tuple(volumes[name] for name in names))

    def measure(self, experiment: Experiment, expected: Resources) -> Usage:
        started = self.clock()
        deadline = started + 30
        if self.inspect(experiment, expected.container_id, deadline=deadline) != expected:
            raise ReadError("physical storage selection differs from retained resources")
        probe = Path(__file__).with_name("guest_usage.py").read_text(encoding="utf-8")
        value = self._json([
            "exec", "--user", "1000:1000", expected.container_id,
            "/usr/bin/python3", "-I", "-c", probe,
        ], deadline)
        if not isinstance(value, dict) or set(value) != {"usage", "free"}:
            raise ReadError("guest measurement fields are incomplete")
        for field in ("usage", "free"):
            counts = value[field]
            if not isinstance(counts, list) or len(counts) != 4 or any(
                type(amount) is not int or amount < 0 for amount in counts
            ):
                raise ReadError("guest measurement counts are malformed")
        if self.inspect(experiment, expected.container_id, deadline=deadline) != expected:
            raise ReadError("physical storage changed during measurement")
        persistent = sum(value["usage"])
        output, free = value["usage"][3], min(value["free"])
        finished = self._check_deadline(deadline)
        return Usage(
            expected, started, finished, persistent, output, free,
        )

    def network_inventory(
        self, experiment: Experiment, container_id: str, *, peers: tuple[str, ...] = (),
        expected: NetworkIdentity | None = None,
    ) -> NetworkIdentity:
        """Observe exact attachments twice; this does not probe egress or grant custody."""
        selected = selected_containers(container_id, peers)
        deadline = self.clock() + 30
        observations: list[NetworkIdentity] = []
        for _ in range(2):
            containers = self._json(["container", "inspect", *selected], deadline)
            networks = self._json([
                "network", "inspect", f"{experiment.resource_prefix}-internal",
            ], deadline)
            try:
                observed = inspect_inventory(experiment, selected, containers, networks)
            except ValueError as error:
                raise ReadError(str(error)) from error
            if expected is not None and observed != expected:
                raise ReadError("physical network differs from retained selection")
            observations.append(observed)
        if observations[0] != observations[1]:
            raise ReadError("physical network changed during observation")
        self._check_deadline(deadline)
        return observations[1]

    def refresh(self, monitor: StorageMonitor) -> None:
        monitor.invalidate()
        if monitor.closed or self.clock() >= monitor.started + monitor.policy.lifetime_seconds:
            raise ReadError("host monitor is closed or its active lifetime has expired")
        monitor.record(self.measure(monitor.experiment, monitor.resources), now=self.clock())

    def volatile_mounts(self, experiment: Experiment, expected: Resources) -> VolatileMounts:
        """Observe effective root/tmpfs posture without qualifying services or source."""
        deadline = self.clock() + 30
        if self.inspect(experiment, expected.container_id, deadline=deadline) != expected:
            raise ReadError("physical storage selection differs from retained resources")
        probe = Path(__file__).with_name("guest_mounts.py").read_text(encoding="utf-8")
        value = self._json([
            "exec", "--user", "1000:1000", expected.container_id,
            "/usr/bin/python3", "-I", "-c", probe,
        ], deadline)
        try:
            observed = inspect_volatile_mounts(experiment, value)
        except ValueError as error:
            raise ReadError(str(error)) from error
        if self.inspect(experiment, expected.container_id, deadline=deadline) != expected:
            raise ReadError("physical storage changed during mount observation")
        self._check_deadline(deadline)
        return observed
