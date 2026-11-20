"""Receipt-pinned roots and governance for the internal watch-owner adapter.

This read-only binding is not process, runtime-slot, or transaction authority.
Lifecycle callers must compose those proofs before using a writer transition.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from zdd.cutover_lock import CutoverLock, lock_path

from . import _installation_context as _context_api
from .watch_notification import positive_seconds

PLUGIN_ID = "agent-pull-requests"
_PINNED_FIELDS = (
    "marketplaceId", "pluginId", "sourceFingerprint", "payloadRoot", "cellRoot",
    "pluginRoot", "versionsRoot", "snapshotsRoot", "stateRoot", "runRoot",
    "logsRoot", "cacheRoot", "launchersRoot", "reposRoot", "namespaceReceipt",
    "installReceipt", "namespaceGeneration", "generation",
)


class _InstallationAPI(Protocol):
    def paths_equal(self, left: str | Path, right: str | Path) -> bool: ...

    def validate_namespace_receipt(
        self, receipt_path: str | Path, durable_home: str | Path,
    ) -> dict[str, Any]: ...

    def validate_context_receipt(
        self, receipt_path: str | Path, durable_home: str | Path, *,
        expected_plugin_id: str | None = None,
        expected_marketplace_id: str | None = None,
        expected_payload_root: str | Path | None = None,
        expected_cell_root: str | Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, Any]: ...

    def resolve_installation_mode(
        self, *, legacy_root: str | Path, context: str | Path, plugin_id: str,
        payload_root: str | Path, durable_home: str | Path,
        expected_marketplace_id: str, expected_cell_root: str | Path,
        environment: Mapping[str, str] | None, os_profile: str | Path,
    ) -> dict[str, Any]: ...


context_api = cast(_InstallationAPI, _context_api)


def _absolute(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise ValueError(f"watch installation {label} must be absolute")
    return path


@dataclass(frozen=True)
class WatchInstallation:
    """Pin canonical receipt evidence without provisioning or adopting state."""

    receipt: Path
    plugin_root: Path
    payload_root: Path
    durable_home: Path
    os_profile: Path
    marketplace_id: str
    state_root: Path
    run_root: Path
    namespace_generation: int
    install_generation: int
    _signature: tuple[Any, ...]
    _creating_pid: int

    @classmethod
    def capture(
        cls, *, receipt: Path, plugin_root: Path, payload_root: Path,
        durable_home: Path, os_profile: Path,
        environment: Mapping[str, str] | None = None,
    ) -> WatchInstallation:
        receipt = _absolute(receipt, "receipt")
        plugin_root = _absolute(plugin_root, "plugin root")
        payload_root = _absolute(payload_root, "payload root")
        durable_home = _absolute(durable_home, "durable home")
        os_profile = _absolute(os_profile, "OS profile")
        validated = context_api.validate_context_receipt(
            receipt, durable_home, expected_plugin_id=PLUGIN_ID,
            expected_payload_root=payload_root, environment=environment,
        )
        if not context_api.paths_equal(validated["pluginRoot"], plugin_root):
            raise PermissionError("watch receipt belongs to another installation")
        cls._require_active(validated, durable_home)
        return cls(
            receipt=Path(validated["installReceipt"]),
            plugin_root=Path(validated["pluginRoot"]),
            payload_root=Path(validated["payloadRoot"]),
            durable_home=durable_home, os_profile=os_profile,
            marketplace_id=validated["marketplaceId"],
            state_root=Path(validated["stateRoot"]), run_root=Path(validated["runRoot"]),
            namespace_generation=validated["namespaceGeneration"],
            install_generation=validated["generation"],
            _signature=tuple(validated[key] for key in _PINNED_FIELDS),
            _creating_pid=os.getpid(),
        )

    @staticmethod
    def _require_active(validated: dict[str, Any], durable_home: Path) -> None:
        namespace = context_api.validate_namespace_receipt(
            validated["namespaceReceipt"], durable_home,
        )
        if (
            validated["state"] != "active"
            or namespace["receipt"]["state"] != "active"
            or namespace["receipt"]["generation"] != validated["namespaceGeneration"]
        ):
            raise PermissionError("watch installation and namespace must remain active")

    def read_identity(
        self, *, environment: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Read stable canonical identity, without granting mutation authority."""
        if os.getpid() != self._creating_pid:
            raise PermissionError("watch installation binding belongs to another process")
        validated = context_api.validate_context_receipt(
            self.receipt, self.durable_home, expected_plugin_id=PLUGIN_ID,
            expected_marketplace_id=self.marketplace_id,
            expected_payload_root=self.payload_root,
            expected_cell_root=self.plugin_root.parent.parent,
            environment=environment,
        )
        self._require_active(validated, self.durable_home)
        if tuple(validated[key] for key in _PINNED_FIELDS) != self._signature:
            raise PermissionError("watch installation receipts changed; resolve again")
        return validated

    def _mode(
        self, *, environment: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        self.read_identity(environment=environment)
        mode = context_api.resolve_installation_mode(
            legacy_root=self.os_profile / f".{PLUGIN_ID}",
            context=self.receipt, plugin_id=PLUGIN_ID,
            payload_root=self.payload_root, durable_home=self.durable_home,
            expected_marketplace_id=self.marketplace_id,
            expected_cell_root=self.plugin_root.parent.parent,
            environment=environment, os_profile=self.os_profile,
        )
        self.read_identity(environment=environment)
        return mode

    def require_current(
        self, *, environment: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Recheck pinned identity and default-off governance at an action boundary."""
        mode = self._mode(environment=environment)
        if (
            mode["status"] != "ready" or mode["reason"] != "namespaced-active"
            or mode["desiredMode"] != "namespaced" or mode["actualMode"] != "namespaced"
            or not context_api.paths_equal(mode["runtimeRoot"], self.plugin_root)
            or not context_api.paths_equal(mode["context"], self.receipt)
            or mode["installGeneration"] != self.install_generation
        ):
            raise PermissionError(
                "watch installation governance blocks mutation: "
                f"{mode['status']} ({mode['reason']})"
            )
        return mode

    def require_cleanup(
        self, *, environment: Mapping[str, str] | None = None,
    ) -> None:
        """Permit only a journal-authorized passive exit, not writer transitions."""
        mode = self._mode(environment=environment)
        if (
            (mode["status"], mode["reason"], mode["desiredMode"]) not in {
                ("ready", "namespaced-active", "namespaced"),
                ("deactivation-required", "deactivation-required", "legacy"),
            }
            or mode["actualMode"] != "namespaced"
            or not context_api.paths_equal(mode["runtimeRoot"], self.plugin_root)
            or not context_api.paths_equal(mode["context"], self.receipt)
            or mode["installGeneration"] != self.install_generation
        ):
            raise PermissionError("watch installation does not permit passive cleanup")

    def evidence(self) -> dict[str, Any]:
        """Pinned installation identity for an independently durable owner journal."""
        return dict(zip(_PINNED_FIELDS, self._signature, strict=True))

    def require_transition_origin(
        self, origin: object, *, guard: CutoverLock,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        """Validate historical installation scope under current admission.

        The transition owner must obtain ``origin`` from its validated private
        journal and separately prove native writer/topology custody. This check
        neither authenticates a caller's claimed origin nor authorizes adoption.
        """
        guard.require_owned()
        if guard.path != lock_path(self.run_root):
            raise PermissionError("watch transition origin requires its installation transaction")
        self.require_current(environment=environment)
        current = self.evidence()
        if not isinstance(origin, dict) or set(origin) != set(current):
            raise PermissionError("watch transition origin has incomplete installation evidence")
        for field in _PINNED_FIELDS:
            value = origin[field]
            if field in {"generation", "namespaceGeneration"}:
                valid = type(value) is int and 0 < value <= current[field]
            elif field == "payloadRoot":
                # Retain this as provenance; do not resolve or execute the old payload.
                valid = isinstance(value, str) and Path(value).is_absolute()
            else:
                valid = type(value) is str and value == current[field]
            if not valid:
                raise PermissionError(f"watch transition origin conflicts at {field}")
        self.require_current(environment=environment)
        guard.require_owned()

    @contextmanager
    def transaction(
        self, *, timeout: float, environment: Mapping[str, str] | None = None,
    ) -> Iterator[CutoverLock]:
        """Serialize the whole owner transaction, then revalidate its binding.

        Pass this exact guard to the shared orchestrator. Reconciliation,
        rollback and on-demand startup use the same installation run root.
        """
        positive_seconds(timeout)
        if os.getpid() != self._creating_pid:
            raise PermissionError("watch installation binding belongs to another process")
        self.require_current(environment=environment)
        lease = CutoverLock(self.run_root)
        lease.acquire(timeout=timeout)
        try:
            self.require_current(environment=environment)
            yield lease
        finally:
            lease.release()

    @contextmanager
    def cleanup_transaction(
        self, *, timeout: float, environment: Mapping[str, str] | None = None,
    ) -> Iterator[CutoverLock]:
        """Acquire the same native guard for restricted pending-custody cleanup."""
        positive_seconds(timeout)
        self.require_cleanup(environment=environment)
        lease = CutoverLock(self.run_root)
        lease.acquire(timeout=timeout)
        try:
            self.require_cleanup(environment=environment)
            yield lease
        finally:
            lease.release()
