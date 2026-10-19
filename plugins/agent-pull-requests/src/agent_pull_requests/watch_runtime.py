"""Completed-slot and exact-instance evidence, not lifecycle authorization."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from zdd import diagnostics, process_exit
from zdd.process_exit import ProcessExitReference

from . import _installation_context as api
from ._selected_runtime import require_selected_metadata
from .watch_installation import PLUGIN_ID, WatchInstallation


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WatchRuntime:
    """Pin a canonical completed runtime, including an older owned incumbent."""

    marketplace_id: str
    plugin_root: Path
    receipt: Path
    snapshot_id: str
    runtime_version: str
    runtime_root: Path
    payload_root: Path
    payload_version: str
    completion_fingerprint: str

    @classmethod
    def capture(
        cls, *, installation: WatchInstallation, snapshot_id: str,
        runtime_version: str, runtime_root: Path, payload_root: Path,
        payload_version: str, environment: Mapping[str, str] | None = None,
    ) -> WatchRuntime:
        installation.require_current(environment=environment)
        runtime = cls.capture_identity(
            installation=installation, snapshot_id=snapshot_id,
            runtime_version=runtime_version, runtime_root=runtime_root,
            payload_root=payload_root, payload_version=payload_version, environment=environment,
        )
        installation.require_current(environment=environment)
        return runtime

    @classmethod
    def capture_identity(
        cls, *, installation: WatchInstallation, snapshot_id: str,
        runtime_version: str, runtime_root: Path, payload_root: Path,
        payload_version: str, environment: Mapping[str, str] | None = None,
    ) -> WatchRuntime:
        """Read completed build identity; never authorize a lifecycle transition."""
        if not runtime_root.is_absolute() or not payload_root.is_absolute():
            raise ValueError("watch runtime roots must be absolute")
        installation.read_identity(environment=environment)
        completed = api.validate_runtime_slot_completion(
            context=installation.receipt,
            expected_marketplace_id=installation.marketplace_id,
            expected_plugin_id=PLUGIN_ID, snapshot_id=snapshot_id,
            runtime_version=runtime_version, expected_payload_root=payload_root,
            expected_payload_version=payload_version,
            durable_home=installation.durable_home, environment={},
        )
        if not api.paths_equal(completed["slotRoot"], runtime_root):
            raise PermissionError("watch runtime does not match its completed slot")
        installation.read_identity(environment=environment)
        return cls(
            installation.marketplace_id, installation.plugin_root, installation.receipt,
            snapshot_id, runtime_version, Path(completed["slotRoot"]), payload_root,
            payload_version, _fingerprint(completed),
        )

    def require_current(
        self, installation: WatchInstallation, *,
        environment: Mapping[str, str] | None = None,
        require_selected: bool = False,
    ) -> None:
        """Compose current governance with immutable historical build evidence."""
        installation.require_current(environment=environment)
        self.require_identity(
            installation, environment=environment, require_selected=require_selected,
        )
        installation.require_current(environment=environment)

    def require_identity(
        self, installation: WatchInstallation, *,
        environment: Mapping[str, str] | None = None,
        require_selected: bool = False,
    ) -> None:
        installation.read_identity(environment=environment)
        if (
            self.marketplace_id != installation.marketplace_id
            or not api.paths_equal(self.plugin_root, installation.plugin_root)
            or not api.paths_equal(self.receipt, installation.receipt)
        ):
            raise PermissionError("watch runtime belongs to another installation")
        completed = api.validate_runtime_slot_completion(
            context=installation.receipt,
            expected_marketplace_id=self.marketplace_id, expected_plugin_id=PLUGIN_ID,
            snapshot_id=self.snapshot_id, runtime_version=self.runtime_version,
            expected_payload_root=self.payload_root, expected_payload_version=self.payload_version,
            durable_home=installation.durable_home, environment={},
        )
        if (
            not api.paths_equal(completed["slotRoot"], self.runtime_root)
            or _fingerprint(completed) != self.completion_fingerprint
        ):
            raise PermissionError("watch runtime completion evidence changed")
        if require_selected:
            require_selected_metadata(
                api, root=installation.plugin_root, runtime_version=self.runtime_version,
                snapshot_id=self.snapshot_id, payload_root=self.payload_root,
                payload_version=self.payload_version, completed=completed,
            )
        installation.read_identity(environment=environment)

    def evidence(self) -> dict[str, str]:
        return {
            "marketplaceId": self.marketplace_id, "pluginId": PLUGIN_ID,
            "pluginRoot": str(self.plugin_root), "installReceipt": str(self.receipt),
            "snapshotId": self.snapshot_id, "runtimeVersion": self.runtime_version,
            "runtimeRoot": str(self.runtime_root), "payloadRoot": str(self.payload_root),
            "payloadVersion": self.payload_version,
            "completionFingerprint": self.completion_fingerprint,
        }

    @classmethod
    def from_evidence(
        cls, value: object, installation: WatchInstallation, *,
        environment: Mapping[str, str] | None = None,
    ) -> WatchRuntime:
        installation.require_current(environment=environment)
        runtime = cls.from_identity_evidence(value, installation, environment=environment)
        installation.require_current(environment=environment)
        return runtime

    @classmethod
    def from_identity_evidence(
        cls, value: object, installation: WatchInstallation, *,
        environment: Mapping[str, str] | None = None,
    ) -> WatchRuntime:
        if not isinstance(value, dict) or any(not isinstance(item, str) for item in value.values()):
            raise ValueError("watch runtime evidence must contain string fields")
        required = {
            "marketplaceId", "pluginId", "pluginRoot", "installReceipt", "snapshotId",
            "runtimeVersion", "runtimeRoot", "payloadRoot", "payloadVersion",
            "completionFingerprint",
        }
        if set(value) != required:
            raise ValueError("watch runtime evidence has unknown or missing fields")
        runtime = cls.capture_identity(
            installation=installation, snapshot_id=value["snapshotId"],
            runtime_version=value["runtimeVersion"], runtime_root=Path(value["runtimeRoot"]),
            payload_root=Path(value["payloadRoot"]), payload_version=value["payloadVersion"],
            environment=environment,
        )
        if runtime.evidence() != value:
            raise PermissionError("watch runtime evidence does not match its installation")
        return runtime


@dataclass(frozen=True)
class WatchInstance:
    """Expected identity obtained from an attributable launch or owner journal.

    Health must come from that instance's authenticated control connection.
    A discovery response alone is not permission to construct an expectation.
    """

    runtime: WatchRuntime
    pid: int
    start_time: str
    instance_id: str

    def __post_init__(self) -> None:
        if type(self.pid) is not int or not 0 < self.pid <= 0xFFFFFFFF:
            raise ValueError("watch instance requires a positive native PID")
        if (
            not isinstance(self.start_time, str) or not 0 < len(self.start_time) <= 64
            or not self.start_time.isascii() or not self.start_time.isdecimal()
        ):
            raise ValueError("watch instance requires a native process start token")
        if (
            not isinstance(self.instance_id, str)
            or str(UUID(self.instance_id)) != self.instance_id
            or UUID(self.instance_id).version != 4
        ):
            raise ValueError("watch instance requires a canonical random instance UUID")

    def evidence(self) -> dict[str, object]:
        return {
            **self.runtime.evidence(), "pid": self.pid,
            "startTime": self.start_time, "instanceId": self.instance_id,
        }

    @classmethod
    def from_evidence(
        cls, value: object, installation: WatchInstallation, *,
        environment: Mapping[str, str] | None = None,
    ) -> WatchInstance:
        installation.require_current(environment=environment)
        instance = cls.from_identity_evidence(value, installation, environment=environment)
        installation.require_current(environment=environment)
        return instance

    @classmethod
    def from_identity_evidence(
        cls, value: object, installation: WatchInstallation, *,
        environment: Mapping[str, str] | None = None,
    ) -> WatchInstance:
        if not isinstance(value, dict):
            raise ValueError("watch instance evidence must be an object")
        runtime_value = {
            key: item for key, item in value.items() if key not in {"pid", "startTime", "instanceId"}
        }
        runtime = WatchRuntime.from_identity_evidence(
            runtime_value, installation, environment=environment,
        )
        instance = cls(runtime, value.get("pid"), value.get("startTime"), value.get("instanceId"))
        if instance.evidence() != value:
            raise PermissionError("watch instance evidence does not match its runtime")
        return instance

    def require_live(
        self, installation: WatchInstallation, *,
        read_health: Callable[[], Mapping[str, object]],
        environment: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        """Revalidate immediately before an authorized exact-instance operation."""
        installation.require_current(environment=environment)
        health = self.require_live_identity(
            installation, read_health=read_health, environment=environment,
        )
        installation.require_current(environment=environment)
        return health

    def require_live_identity(
        self, installation: WatchInstallation, *,
        read_health: Callable[[], Mapping[str, object]],
        environment: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        """Read exact native identity and authenticated health, without authority."""
        self.runtime.require_identity(installation, environment=environment)
        if diagnostics.process_start_time(self.pid) != self.start_time:
            raise ProcessLookupError("watch instance native identity changed or is unavailable")
        health = read_health()
        if health.get("instance") != self.evidence():
            raise PermissionError("watch instance health does not match expected ownership")
        self.runtime.require_identity(installation, environment=environment)
        if diagnostics.process_start_time(self.pid) != self.start_time:
            raise ProcessLookupError("watch instance exited during ownership validation")
        return health

    def capture_exit(
        self, installation: WatchInstallation, *,
        read_health: Callable[[], Mapping[str, object]],
        environment: Mapping[str, str] | None = None,
    ) -> ProcessExitReference:
        """Capture native exit evidence BEFORE any graceful lifecycle control.

        The returned reference cannot signal or grant transaction authority.
        """
        installation.require_current(environment=environment)
        reference = self.capture_identity_exit(
            installation, read_health=read_health, environment=environment,
        )
        try:
            installation.require_current(environment=environment)
            return reference
        except BaseException:
            reference.close()
            raise

    def capture_identity_exit(
        self, installation: WatchInstallation, *,
        read_health: Callable[[], Mapping[str, object]],
        environment: Mapping[str, str] | None = None,
    ) -> ProcessExitReference:
        """Capture a non-signaling reference; cleanup authority is separate."""
        reference = process_exit.open_process(self.pid, self.start_time)
        try:
            self.require_live_identity(
                installation, read_health=read_health, environment=environment,
            )
            if reference.wait(0):
                raise ProcessLookupError("watch instance exited during reference capture")
            return reference
        except BaseException:
            reference.close()
            raise
