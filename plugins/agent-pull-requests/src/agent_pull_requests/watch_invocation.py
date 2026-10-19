"""Physical running-slot evidence for the governed watch-owner entry point.

No discovery response or environment override can attest the running process.
This module neither selects a runtime nor provisions, starts or activates one.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from uuid import uuid4

from zdd import diagnostics

from . import _installation_context as api
from .watch_installation import PLUGIN_ID, WatchInstallation
from .watch_runtime import WatchInstance, WatchRuntime


def _beneath(path: Path, root: Path) -> bool:
    return path.is_absolute() and api.path_is_within(path, root)


def _running_slot(installation: WatchInstallation) -> Path:
    slot = Path(sys.prefix)
    versions = Path(installation.evidence()["versionsRoot"])
    if (
        not slot.is_absolute() or not api.paths_equal(slot.parent, versions)
        or api._is_link_or_junction(slot)
        or not (slot / "pyvenv.cfg").is_file()
        or api.paths_equal(sys.base_prefix, slot)
    ):
        raise PermissionError("watch interpreter is not in this installation's runtime slot")
    python = Path(sys.executable)
    scripts = slot / ("Scripts" if os.name == "nt" else "bin")
    # POSIX venv interpreter leaves may be symlinks to the base interpreter.
    if (
        not python.is_absolute() or not python.is_file()
        or not api.paths_equal(python.parent, scripts)
        or python.name not in {"python", "python3", "python.exe", "pythonw.exe"}
    ):
        raise PermissionError("watch executable does not belong to its running venv")
    return slot


def _require_modules(slot: Path) -> None:
    import agent_procutil
    import single_instance_lease
    import work_coalescing_singleton
    import zdd

    from importlib import import_module

    for name in ("__main__", "watch_daemon", "watch_journal", "watch_writer"):
        import_module(f"{__package__}.{name}")

    roots = {
        Path(sysconfig.get_path(key)) for key in ("purelib", "platlib")
    }
    if any(not _beneath(root, slot) for root in roots):
        raise PermissionError("watch installation library roots escape the running slot")
    packages = (
        __package__, agent_procutil.__name__, single_instance_lease.__name__,
        work_coalescing_singleton.__name__, zdd.__name__,
    )
    modules = (
        module for name, module in tuple(sys.modules.items())
        if any(name == package or name.startswith(package + ".") for package in packages)
    )
    for module in modules:
        source = getattr(module, "__file__", None)
        if not isinstance(source, str) or not any(_beneath(Path(source), root) for root in roots):
            raise PermissionError("watch runtime modules escape the completed slot")
        if not Path(source).is_file():
            raise PermissionError("watch runtime module is unavailable")


def capture_running(
    installation: WatchInstallation, *, environment: Mapping[str, str] | None = None,
    require_selected: bool = False,
) -> WatchRuntime:
    """Attest the *actual* interpreter and loaded owner modules, then pin its build.

    Receipt hints are validated by the canonical completion API before they
    become evidence. Historical incumbents use their immutable snapshot payload,
    not the current management payload's version.
    """
    from . import __version__

    installation.require_current(environment=environment)
    slot = _running_slot(installation)
    _require_modules(slot)
    ownership = api.read_json(slot / api.RUNTIME_SLOT_OWNERSHIP_FILE)
    if not isinstance(ownership, dict) or not isinstance(ownership.get("snapshot"), dict):
        raise ValueError("watch running slot lacks snapshot identity")
    snapshot_id = ownership["snapshot"].get("id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("watch running slot lacks snapshot identity")
    validated_slot = api.validate_runtime_slot_ownership(
        context=installation.receipt, expected_marketplace_id=installation.marketplace_id,
        expected_plugin_id=PLUGIN_ID, snapshot_id=snapshot_id, runtime_version=slot.name,
        durable_home=installation.durable_home, environment={},
    )
    provenance = api.read_json(validated_slot["snapshotProvenance"])
    payload = provenance.get("payload") if isinstance(provenance, dict) else None
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("root"), str)
        or not isinstance(payload.get("version"), str)
    ):
        raise ValueError("watch running snapshot lacks payload identity")
    runtime = WatchRuntime.capture(
        installation=installation, snapshot_id=snapshot_id,
        runtime_version=slot.name, runtime_root=slot,
        payload_root=Path(payload["root"]), payload_version=payload["version"],
        environment=environment,
    )
    if __version__ != runtime.runtime_version:
        raise PermissionError("watch loaded package version differs from the completed slot")
    if _running_slot(installation) != slot:
        raise PermissionError("watch running interpreter changed during validation")
    _require_modules(slot)
    runtime.require_current(
        installation, environment=environment, require_selected=require_selected,
    )
    return runtime


@dataclass(frozen=True)
class WatchInvocation:
    installation: WatchInstallation
    runtime: WatchRuntime
    raw_context: str

    @classmethod
    def capture(cls, *, require_selected: bool = False) -> WatchInvocation | None:
        raw_context = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
        if not raw_context:
            return None
        root = Path(os.environ.get("AGENT_RT_ROOT", ""))
        payload = Path(os.environ.get("COPILOT_PLUGIN_ROOT", ""))
        if (
            not root.is_absolute() or root.name != PLUGIN_ID
            or root.parent.name != "plugins"
            or root.parent.parent.parent.name != "marketplaces"
            or not payload.is_absolute()
        ):
            raise PermissionError("watch explicit context requires canonical invocation roots")
        durable = root.parent.parent.parent.parent
        # Use the same OS-account-home policy as canonical installation governance,
        # never an inherited HOME pointing at another installation cell.
        _, profile = api._current_environment(
            environment=os.environ, os_profile=None, platform=None, wsl_distro=None,
        )
        validated = api.validate_context_receipt(
            raw_context, durable, expected_plugin_id=PLUGIN_ID,
            expected_payload_root=payload, expected_cell_root=root.parent.parent,
        )
        installation = WatchInstallation.capture(
            receipt=Path(validated["installReceipt"]), plugin_root=root,
            payload_root=payload, durable_home=durable, os_profile=profile,
        )
        return cls(
            installation, capture_running(installation, require_selected=require_selected),
            raw_context,
        )

    def require_current(self) -> None:
        installation = self.fresh_installation()
        if capture_running(installation, environment={}) != self.runtime:
            raise PermissionError("watch invocation running build changed")

    def fresh_installation(self) -> WatchInstallation:
        """Rebind current governance in this known cell, not the old launch payload."""
        installation = self.fresh_identity()
        installation.require_current(environment={})
        return installation

    def fresh_identity(self) -> WatchInstallation:
        """Read the current known-cell receipt without authorizing effects."""
        validated = api.validate_context_receipt(
            self.installation.receipt, self.installation.durable_home,
            expected_plugin_id=PLUGIN_ID,
            expected_marketplace_id=self.installation.marketplace_id,
            expected_cell_root=self.installation.plugin_root.parent.parent, environment={},
        )
        if (
            not api.paths_equal(validated["stateRoot"], self.installation.state_root)
            or not api.paths_equal(validated["runRoot"], self.installation.run_root)
        ):
            raise PermissionError("watch invocation state or run root changed")
        installation = WatchInstallation.capture(
            receipt=self.installation.receipt, plugin_root=self.installation.plugin_root,
            payload_root=Path(validated["payloadRoot"]),
            durable_home=self.installation.durable_home,
            os_profile=self.installation.os_profile, environment={},
        )
        return installation

    def instance(self, *, instance_id: str | None = None) -> WatchInstance:
        self.require_current()
        pid = os.getpid()
        birth = diagnostics.process_start_time(pid)
        if birth is None:
            raise ProcessLookupError("watch running process lacks native birth evidence")
        return WatchInstance(
            self.runtime, pid, birth, str(uuid4()) if instance_id is None else instance_id,
        )

    def peer_prefix(self, peer_launch: ModuleType) -> list[str]:
        """Compose same-cell authentication without writing into its snapshot."""
        self.require_current()
        prefix = peer_launch.launch_prefix(
            PLUGIN_ID, self.installation.plugin_root,
            str(self.installation.receipt), "agent-worktrees",
        )
        return [prefix[0], "-B", *prefix[1:]]
