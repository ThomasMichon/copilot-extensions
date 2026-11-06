"""One finite central-Vault retirement through shared native process custody."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from agent_procutil._daemon_instance_identity import identity_state, path_identity
from agent_procutil.daemon_instances import inspect_instances
from agent_procutil.process_retirement import PinnedProcess, RetirementRefused

from .config import installation_id
from .rendezvous import Endpoint
from .service_handoff import peer_identity

GRACE_SECONDS = 30.0
_CUSTODY = (
    "instance_id", "pid", "birth", "service", "purpose", "lifecycle", "owner",
    "owner_pid", "owner_birth", "run_id", "source", "source_member",
    "runtime_root", "runtime_device", "runtime_inode", "protected",
)


class Predecessor:
    def __init__(
        self, endpoint: Endpoint, root: Path, record: dict[str, str],
        registry_root: Path | None,
    ) -> None:
        self.endpoint = endpoint
        self.root = root
        self.record = dict(record)
        self.registry_root = registry_root

    @classmethod
    def capture(
        cls, endpoint: Endpoint, root: Path, registry_root: Path | None = None,
    ) -> Predecessor | None:
        """Capture one admitted service owner, not an arbitrary endpoint PID."""
        try:
            if (
                endpoint.pid is None or endpoint.pid == os.getpid()
                or endpoint.installation_id != installation_id()
            ):
                return None
            records = inspect_instances(registry_root)
            candidates = [
                record for record in records
                if record.get("pid") == str(endpoint.pid)
            ]
            if len(candidates) != 1:
                return None
            result = cls(endpoint, root, candidates[0], registry_root)
            return result if result._owned(records) else None
        except (OSError, ValueError, KeyError, RuntimeError):
            return None

    def _owned(self, records: list[dict[str, str]]) -> bool:
        candidates = [
            item for item in records if item.get("pid") == str(self.endpoint.pid)
        ]
        record = candidates[0] if len(candidates) == 1 else None
        if (
            record is None or record.get("state") != "running"
            or any(record.get(key) != self.record.get(key) for key in _CUSTODY)
            or record.get("service") != "agent-vault"
            or record.get("lifecycle") != "resident"
            or record.get("protected") != "0" or record.get("source_member")
        ):
            return False
        if record.get("purpose") != "production":
            incoming = [
                item for item in records
                if item.get("pid") == str(os.getpid()) and item.get("state") == "running"
            ]
            if (
                len(incoming) != 1 or not record.get("run_id")
                or any(record.get(key) != incoming[0].get(key) for key in (
                    "purpose", "run_id", "owner_pid", "owner_birth",
                ))
            ):
                return False
        source, runtime = Path(record["source"]), Path(record["runtime_root"])
        root = self.root
        if (
            root.resolve(strict=True) != root
            or source.resolve(strict=True) != source
            or not source.is_file()
            or runtime.resolve(strict=True) != runtime
            or source.parent.name != "agent_vault"
            or source.name not in {"service.py", "launch.py"}
            or runtime not in {root, source.parent}
            or path_identity(runtime) != (
                record["runtime_device"], record["runtime_inode"],
            )
        ):
            return False
        relative = source.relative_to(root / "versions")
        if len(relative.parts) < 3:
            return False
        # Native peer admission enforces the current OS principal as well as birth.
        peer_identity(self.endpoint.pid)
        return identity_state(self.endpoint.pid, record["birth"]) == "matching"

    def exited(self) -> bool:
        """A different native incarnation cannot still own the old writer."""
        try:
            return identity_state(self.endpoint.pid, self.record["birth"]) in {"dead", "changed"}
        except (OSError, ValueError):
            return False

    def retire(self, current: Callable[[], bool]) -> str:
        """Revalidate selection and central-role custody at the native mutation."""
        try:
            if self.exited():
                return "exited"
            if not current() or not self._owned(inspect_instances(self.registry_root)):
                return "refused-owner-or-selection"
            with PinnedProcess(self.endpoint.pid, self.record["birth"]) as process:
                if process.exited():
                    return "exited"
                if not current() or not self._owned(inspect_instances(self.registry_root)):
                    return "refused-owner-or-selection"
                process.terminate(timeout=5)
            return "cold-retired"
        except (OSError, ValueError, KeyError, RuntimeError, RetirementRefused):
            return "refused-native-retirement"
