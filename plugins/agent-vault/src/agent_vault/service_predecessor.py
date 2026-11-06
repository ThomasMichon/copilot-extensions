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
        self, pid: int, root: Path, record: dict[str, str],
        registry_root: Path | None,
    ) -> None:
        self.pid = pid
        self.root = root
        self.record = dict(record)
        self.registry_root = registry_root

    @classmethod
    def capture(
        cls, endpoint: Endpoint, root: Path, registry_root: Path | None = None,
    ) -> Predecessor | None:
        """Capture one admitted service owner, not an arbitrary endpoint PID."""
        if endpoint.installation_id != installation_id():
            return None
        return cls.capture_pid(endpoint.pid, root, registry_root)

    @classmethod
    def capture_pid(
        cls, pid: int | None, root: Path, registry_root: Path | None = None,
    ) -> Predecessor | None:
        """A lease PID identifies a candidate, not native or service authority."""
        try:
            if type(pid) is not int or pid <= 0 or pid == os.getpid():
                return None
            records = inspect_instances(registry_root)
            candidates = [
                record for record in records
                if record.get("pid") == str(pid)
            ]
            if len(candidates) != 1:
                return None
            result = cls(pid, root, candidates[0], registry_root)
            return result if result._owned(records) else None
        except (OSError, ValueError, KeyError, RuntimeError):
            return None

    def _owned(self, records: list[dict[str, str]]) -> bool:
        candidates = [
            item for item in records if item.get("pid") == str(self.pid)
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
        peer_identity(self.pid)
        return identity_state(self.pid, record["birth"]) == "matching"

    def exited(self) -> bool:
        """A different native incarnation cannot still own the old writer."""
        try:
            return identity_state(self.pid, self.record["birth"]) in {"dead", "reused"}
        except (OSError, ValueError):
            return False

    def retire(self, current: Callable[[], bool]) -> str:
        """Revalidate selection and central-role custody at the native mutation."""
        try:
            if self.exited():
                return "exited"
            if not current() or not self._owned(inspect_instances(self.registry_root)):
                return "refused-owner-or-selection"
            with PinnedProcess(self.pid, self.record["birth"]) as process:
                if process.exited():
                    return "exited"
                process.pause()
                if not current() or not self._owned(inspect_instances(self.registry_root)):
                    return "refused-owner-or-selection"
                if _has_children(self.pid):
                    return "refused-live-workers"
                process.terminate(timeout=5)
            return "cold-retired"
        except (OSError, ValueError, KeyError, RuntimeError, RetirementRefused):
            return "refused-native-retirement"


def _has_children(pid: int) -> bool:
    """The pinned, paused parent cannot launch another credential subprocess."""
    try:
        import psutil
    except ImportError as error:
        raise RetirementRefused("native worker census unavailable") from error
    try:
        children = psutil.Process(pid).children()
        console = _console_host_path() if children else None
        for child in children:
            if console is None:
                return True
            image = Path(child.exe())
            if image.parent != console.parent or not image.samefile(console):
                return True
        return False
    except psutil.Error as error:
        raise RetirementRefused("native worker census unconfirmed") from error


def _console_host_path() -> Path | None:
    """Only the native system console host is known not to be credential work."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel.GetSystemDirectoryW.restype = wintypes.UINT
    directory = ctypes.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(directory, len(directory))
    if not 0 < length < len(directory):
        raise RetirementRefused("native system directory unavailable")
    return Path(directory.value) / "conhost.exe"
