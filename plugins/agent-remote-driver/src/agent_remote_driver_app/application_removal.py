"""Compose original application receipts; never replay an uncertain native effect."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import re
import shutil
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, Callable

from agent_procutil.daemon_instances import DaemonInstanceReceipt, default_registry_root
from agent_procutil.daemon_observation import observe_instances
from agent_procutil.process_retirement import PinnedProcess
from single_instance_lease.operation import OperationLock

from . import connector
from . import service_lifecycle as lifecycle


class RemovalUncertain(RuntimeError):
    """Original intent exists without a durably confirmed native outcome."""

    def __init__(self, phase: str) -> None:
        self.phase = phase
        super().__init__(f"Driver removal {phase} outcome is uncertain; retain original evidence")


class RuntimeReferenceHeld(RuntimeError):
    """An independent daemon's original owner must settle its artifacts first."""


def main(
    *, provider: ModuleType, slot: Path, workspace: Path, arguments: list[str],
    deadline: float, global_bin: Path | None = None,
) -> dict[str, str]:
    """Public callback, reached only through the canonical external-interpreter route."""
    parser = argparse.ArgumentParser(prog="agent-remote-driver uninstall", allow_abbrev=False)
    parser.add_argument("--instance-id", required=True)
    options = parser.parse_args(arguments)
    instance_id = options.instance_id
    if re.fullmatch(r"[a-f0-9]{32}", instance_id) is None:
        raise ValueError("Driver removal requires its original exact instance id")
    packet = workspace / "packet"
    if os.path.lexists(packet):
        plan = provider.read_application_removal(packet, deadline=deadline)
        if (
            plan.slot != slot or plan.component != "agent-remote-driver"
            or global_bin is not None
            and provider._artifacts(packet)._native(global_bin)
            != provider._artifacts(packet)._native(plan.command_directory)
        ):
            raise ValueError("Driver removal original packet selection changed")
    else:
        plan = provider.plan_application_removal(
            slot, component="agent-remote-driver", global_bin=global_bin, deadline=deadline,
        )
        endpoint = slot / ".runtime-bootstrap" / f"driver-service-{instance_id}.json"
        provider.prepare_application_removal(
            plan, packet, deadline=deadline, pending_artifacts=(endpoint,),
        )
    return remove(packet, workspace / "journal", instance_id, deadline=deadline)


def _original_snapshot(original: Any, plan: Any, instance_id: str, artifacts: ModuleType) -> None:
    fields = {
        "instance_id", "pid", "birth", "runtime_root", "registry_root", "registry_state",
        "registration_state", "endpoint", "endpoint_state",
    }
    if (
        not isinstance(original, dict) or set(original) != fields
        or original["instance_id"] != instance_id
        or type(original["pid"]) is not int or not 0 < original["pid"] < 2**32
        or not isinstance(original["birth"], str) or not 0 < len(original["birth"]) <= 4096
        or any(
            not isinstance(original[key], str) or not Path(original[key]).is_absolute()
            or ".." in Path(original[key]).parts
            for key in ("runtime_root", "registry_root", "endpoint")
        )
        or any(
            not isinstance(original[key], dict)
            or set(original[key]) != {"kind", "device", "inode", "mode", "digest", "size"}
            or original[key]["kind"] != kind
            or any(type(original[key][field]) is not int or original[key][field] < 0
                   for field in ("device", "inode", "mode", "size"))
            or not isinstance(original[key]["digest"], str)
            or (original[key]["digest"] != "" if kind == "directory" else
                re.fullmatch(r"[a-f0-9]{64}", original[key]["digest"]) is None)
            for key, kind in (
                ("registry_state", "directory"),
                ("registration_state", "file"), ("endpoint_state", "file"),
            )
        )
        or artifacts._native(Path(original["runtime_root"])) != artifacts._native(plan.slot)
        or artifacts._native(Path(original["endpoint"])) != artifacts._native(
            plan.slot / ".runtime-bootstrap" / f"driver-service-{instance_id}.json",
        )
    ):
        raise ValueError("Driver removal original native snapshot is invalid")


def _snapshot(plan: Any, instance_id: str, artifacts: ModuleType, packet: Path,
              deadline: float) -> dict[str, Any]:
    records = observe_instances(service=lifecycle.SERVICE)
    originals = [row for row in records if row.get("instance_id") == instance_id]
    if len(originals) != 1:
        raise RuntimeError("Driver removal original service registration is unconfirmed")
    original = originals[0]
    if (
        original.get("service") != lifecycle.SERVICE
        or not lifecycle._same_directory(Path(original.get("runtime_root", "")), plan.slot)
        or original.get("process_identity_state") not in {"matching", "dead"}
        or original.get("protected", "0") != "0"
        or not original.get("pid", "").isdigit()
        or not 0 < int(original["pid"]) < 2**32
        or not original.get("birth")
        or not isinstance(original.get(lifecycle.LOCATOR), str)
        or any(
            row.get("instance_id") != instance_id
            and lifecycle._same_directory(Path(row.get("runtime_root", "")), plan.slot)
            and row.get("process_identity_state") != "dead" for row in records
        )
    ):
        raise RuntimeError("Driver removal original service identity is unconfirmed")
    endpoint = plan.slot / ".runtime-bootstrap" / f"driver-service-{instance_id}.json"
    if Path(original[lifecycle.LOCATOR]) != endpoint and not lifecycle._same_directory(
        Path(original[lifecycle.LOCATOR]), endpoint,
    ):
        raise ValueError("Driver removal original endpoint differs from its sealed application")
    endpoint_state = artifacts._state(endpoint, deadline)
    pending = artifacts._record(packet / "removal-packet.json", deadline)["pending_artifacts"]
    if pending != {endpoint.relative_to(plan.slot).as_posix(): endpoint_state}:
        raise RuntimeError("Driver removal pending original service artifact differs")
    registry = default_registry_root()
    receipt = DaemonInstanceReceipt(instance_id, registry)
    return {
        "instance_id": instance_id, "pid": int(original["pid"]), "birth": original["birth"],
        "runtime_root": original["runtime_root"], "registry_root": str(registry),
        "registry_state": artifacts._state(registry, deadline),
        "registration_state": artifacts._state(receipt.record_path, deadline),
        "endpoint": str(endpoint), "endpoint_state": endpoint_state,
    }


def _settled_original(plan: Any, original: dict[str, Any], artifacts: ModuleType,
                      deadline: float) -> None:
    registry = Path(original["registry_root"])
    if (
        default_registry_root() != registry
        or artifacts._state(registry, deadline) != original["registry_state"]
    ):
        raise RuntimeError("Driver removal original registry identity changed")
    receipt = DaemonInstanceReceipt(original["instance_id"], registry)
    if os.path.lexists(receipt.record_path) or os.path.lexists(original["endpoint"]):
        raise RuntimeError("Driver removal original service artifacts are not settled")
    for row in observe_instances(registry, service=lifecycle.SERVICE):
        runtime = Path(row.get("runtime_root", ""))
        if (
            runtime.name == plan.slot.name
            and lifecycle._same_directory(runtime.parent, plan.slot.parent)
            and row.get("process_identity_state") != "dead"
        ):
            raise RuntimeError("Driver removal service generation has an unresolved owner")
    try:
        with PinnedProcess(original["pid"], original["birth"]) as process:
            if not process.exited():
                raise RuntimeError("Driver removal original service remains live")
    except ProcessLookupError:
        pass


def _retire_original(
    plan: Any, original: dict[str, Any], artifacts: ModuleType, deadline: float,
) -> None:
    registry = Path(original["registry_root"])
    receipt = DaemonInstanceReceipt(original["instance_id"], registry)
    if (
        default_registry_root() != registry
        or artifacts._state(registry, deadline) != original["registry_state"]
        or artifacts._state(receipt.record_path, deadline) != original["registration_state"]
        or artifacts._state(Path(original["endpoint"]), deadline) != original["endpoint_state"]
    ):
        raise RuntimeError("Driver removal original native identity changed before retirement")
    lifecycle._settle_service_removal_locked(
        plan, original["instance_id"], deadline=deadline,
    )


def _native_phase(
    journal: Path, phase: str, evidence: dict[str, Any], artifacts: ModuleType,
    deadline: float, effect: Callable[[], Any],
) -> bool:
    intent, settled = journal / f"{phase}.intent.json", journal / f"{phase}.settled.json"
    if os.path.lexists(intent):
        if artifacts._record(intent, deadline) != evidence:
            raise RuntimeError("Driver removal original phase intent changed")
        if not os.path.lexists(settled):
            raise RemovalUncertain(phase)
        if artifacts._record(settled, deadline) != evidence:
            raise RuntimeError("Driver removal original settled phase changed")
        return False
    if os.path.lexists(settled):
        raise RuntimeError("Driver removal settled phase has no original intent")
    artifacts._write_once(intent, evidence, deadline)
    try:
        effect()
        artifacts._write_once(settled, evidence, deadline)
    except Exception as error:
        raise RemovalUncertain(phase) from error
    return True


def remove(
    packet: Path, journal: Path, instance_id: str, *, deadline: float,
    home: Path | None = None, executable: str | None = None,
) -> dict[str, str]:
    """Remove one original Driver application from externally retained controls.

    Only the canonical filesystem phases may reconcile partial effects. Native
    service/connector intent without settled evidence always needs its original
    owner's reconciliation, even when a read-only observation looks absent.
    """
    if re.fullmatch(r"[a-f0-9]{32}", instance_id) is None:
        raise ValueError("Driver removal requires its original exact instance id")
    provider = lifecycle.load_provider(packet)
    plan = provider.read_application_removal(packet, deadline=deadline)
    artifacts = provider._artifacts(packet)
    artifacts._deadline(deadline)
    if plan.component != "agent-remote-driver":
        raise ValueError("Driver removal packet belongs to another application")
    for path in (packet, journal):
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("Driver removal controls must be absolute ordinary paths")
    if (
        journal.resolve().is_relative_to(plan.root.resolve())
        or journal.resolve().is_relative_to(packet.resolve())
        or any(Path(path).resolve().is_relative_to(plan.root.resolve())
               for path in (__file__, sys.executable, sys.prefix))
    ):
        raise ValueError("Driver removal helper and transaction must be outside the application")
    artifacts._plain(journal.parent)
    home = home or Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot")
    if not home.is_absolute() or ".." in home.parts:
        raise ValueError("Driver removal connector home must be absolute")
    executable = executable or shutil.which("copilot")
    if not executable:
        raise RuntimeError("Copilot CLI is required for Driver removal")
    evidence = {
        "schema": "copilot-driver.application-removal", "version": 1,
        "packet": str(packet), "packet_state": artifacts._state(
            packet / "removal-packet.json", deadline,
        ),
        "root": str(plan.root), "generation": plan.slot.name,
        "payload_hash": plan.payload_hash, "profile_lock_id": plan.profile_lock_id,
        "instance_id": instance_id, "home": str(home), "executable": executable,
    }
    with OperationLock(journal.parent / (journal.name + ".lock")):
        with OperationLock(plan.root / "driver-service-start.lock"):
            artifacts._deadline(deadline)
            transaction = journal / "transaction.json"
            if os.path.lexists(journal):
                artifacts._plain(journal)
                record = artifacts._record(transaction, deadline)
                if record.get("request") != evidence or not isinstance(record.get("original"), dict):
                    raise RuntimeError("Driver removal original transaction request changed")
                original = record["original"]
            else:
                provider._removal_unchanged(plan)
                original = _snapshot(plan, instance_id, artifacts, packet, deadline)
                _original_snapshot(original, plan, instance_id, artifacts)
                journal.mkdir()
                record = {"request": evidence, "original": original}
                artifacts._write_once(transaction, record, deadline)
            _original_snapshot(original, plan, instance_id, artifacts)
            _native_phase(
                journal, "service", record, artifacts, deadline,
                lambda: _retire_original(plan, original, artifacts, deadline),
            )
            _settled_original(plan, original, artifacts, deadline)

            with ExitStack() as custody:
                def withdraw_connector() -> None:
                    if lifecycle._selected(plan) != plan.slot.name:
                        raise RuntimeError("Driver removal original application is no longer selected")
                    runtime, _engine = provider._resources(packet)
                    python = runtime.slot_python(plan.root, plan.slot.name)
                    if python is None:
                        raise RuntimeError("Driver removal original interpreter is unconfirmed")
                    binding = SimpleNamespace(root=plan.root, slot=plan.slot, python=python)
                    result = custody.enter_context(connector.withdrawal_custody(
                        binding, deadline=deadline, home=home, executable=executable,
                    ))
                    if result != {"withdrawn": True, "assets_retained": True}:
                        raise RuntimeError("Driver removal native connector outcome is unconfirmed")

                if not _native_phase(
                    journal, "connector", record, artifacts, deadline, withdraw_connector,
                ):
                    custody.enter_context(connector.withdrawal_custody(
                        plan, deadline=deadline, home=home, executable=executable, withdraw=False,
                    ))
                def references() -> None:
                    artifacts._deadline(deadline)
                    for owner in observe_instances(Path(original["registry_root"])):
                        runtime_root = Path(owner.get("runtime_root", ""))
                        if runtime_root.is_absolute() and any(
                            lifecycle._same_directory(path, plan.slot)
                            for path in (runtime_root, *runtime_root.parents)
                        ):
                            raise RuntimeReferenceHeld(
                                "Driver removal independent runtime artifacts require their original owner"
                            )

                references()
                withdrawal = journal / "publications.json"
                provider.withdraw_application_publications(packet, withdrawal, deadline=deadline)
                provider.reclaim_application_removal(
                    packet, withdrawal, deadline=deadline, validate=references,
                )
    return {"state": "removed", "instance_id": instance_id, "generation": plan.slot.name}
