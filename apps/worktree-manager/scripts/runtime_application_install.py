"""Slot-local install entry: stage is environment-only; regular install activates."""
from __future__ import annotations

import argparse
import ast as ast
import base64
from contextlib import ExitStack, contextmanager
from dataclasses import replace as replace
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess as subprocess
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
from typing import Any, Callable, Iterator, NamedTuple, cast

_QUALIFICATION_TIMEOUT = 600
_PUBLICATION_ALLOWANCE = 60
APPLICATION_INSTALL_CAPABILITIES = ("global-bin/v1",)


def publication_directory(directory: Path, *, root: Path | None = None) -> Path:
    """Validate an explicit publication selection without creating its ancestry."""
    return cast(Path, _qualification().publication_directory(
        sys.modules[__name__], directory, root=root,
    ))


def require_publication_selection(helper: Path) -> None:
    """Read support from the selected helper, without executing a preflight."""
    _qualification().require_publication_selection(sys.modules[__name__], helper)


class ApplicationStartupUncertain(RuntimeError):
    """Startup was invoked; only its application owner can reconcile its effects."""

    def __init__(self, slot: Path) -> None:
        self.slot = slot
        super().__init__(
            "application startup outcome is unknown; selection and launchers retained "
            "for application-owned reconciliation"
        )


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"application runtime resource is unavailable: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _read(path: Path, *, limit: int = 65536) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > limit:
        raise RuntimeError(f"application publication target is not an ordinary file: {path}")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise RuntimeError(f"application publication target exceeds its byte bound: {path}")
    return content


def _write(path: Path, content: bytes, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if executable or path.suffix == ".sh" or not path.suffix:
            temporary.chmod(0o700)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _qualification() -> ModuleType:
    """Keep copied qualification rules bound to this entry's current hooks."""
    directory = Path(__file__).parent
    if (directory / "runtime").is_dir():
        directory /= "runtime"
    path = directory / "runtime_application_qualification.py"
    module = sys.modules.get("_application_qualification")
    if module is None or Path(module.__file__ or "") != path:
        module = _load(path, "_application_qualification")
    return module


def _materialize_interpreter(slot: Path) -> None:
    _qualification()._materialize_interpreter(sys.modules[__name__], slot)


def _application(slot: Path) -> dict[str, Any]:
    return cast(dict[str, Any], _qualification()._application(sys.modules[__name__], slot))


def callback_timeouts(record: dict[str, Any]) -> dict[str, float]:
    """Read finite per-phase process budgets without changing legacy defaults."""
    return cast(dict[str, float], _qualification().callback_timeouts(sys.modules[__name__], record))


def application_install_timeout(record: dict[str, Any]) -> float:
    """Leave publication/qualification room outside explicitly budgeted callbacks."""
    return cast(float, _qualification().application_install_timeout(sys.modules[__name__], record))


def _validate_application(record: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], _qualification()._validate_application(sys.modules[__name__], record))


def removal_callback(value: Any) -> str:
    """Require one explicit application-owned removal entry point."""
    return cast(str, _qualification().removal_callback(sys.modules[__name__], value))


class InstalledApplicationSource(NamedTuple):
    """Source/profile association, not application health or root admission."""

    slot: Path
    root: Path
    python: Path
    identity: object
    payload_hash: str
    profile_lock_id: str


class ApplicationRemovalPlan(NamedTuple):
    """Observed Source/profile and publications; not mutation or deletion authority."""

    slot: Path
    root: Path
    command_directory: Path
    component: str
    payload_hash: str
    profile_lock_id: str
    directory_identities: tuple[tuple[Path, int, int], ...]
    publications: tuple[tuple[Path, bytes], ...]


def _removal_directories(slot: Path, commands: Path) -> tuple[tuple[Path, int, int], ...]:
    identities = []
    for path in (slot.parent.parent, slot.parent, slot, slot / ".venv", commands):
        publication_directory(path)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("application removal requires ordinary installation directories")
        identities.append((path, info.st_dev, info.st_ino))
    return tuple(identities)


def _removal_unchanged(plan: ApplicationRemovalPlan) -> None:
    if _removal_directories(plan.slot, plan.command_directory) != plan.directory_identities:
        raise RuntimeError("application removal directory identity changed")
    if any(_read(path) != content for path, content in plan.publications):
        raise RuntimeError("application removal publication changed")


def _external_removal_owner(root: Path) -> None:
    if any(Path(path).resolve().is_relative_to(root.resolve())
           for path in (__file__, sys.executable, sys.prefix)):
        raise ValueError("application removal helper and interpreter must be outside the installation")


def plan_application_removal(
    slot: Path, *, component: str, global_bin: Path | None = None,
    deadline: float | None = None,
) -> ApplicationRemovalPlan:
    """Inspect one selected ordinary application, without importing it or deleting.

    Removal must be driven by a helper and interpreter outside the installation.
    The eventual mutation owner must reacquire canonical custody and revalidate
    this observation; a plan alone never permits withdrawal or reclamation.
    """
    deadline = time.monotonic() + _QUALIFICATION_TIMEOUT if deadline is None else deadline

    def check_deadline() -> None:
        if not math.isfinite(deadline) or time.monotonic() >= deadline:
            raise TimeoutError("application removal preflight deadline expired")

    check_deadline()
    if (
        not slot.is_absolute() or ".." in slot.parts or slot.parent.name != "versions"
        or re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", slot.name) is None
        or slot.name.endswith(".")
    ):
        raise ValueError("application removal requires an absolute numbered version slot")
    root = slot.parent.parent
    directory = global_bin or Path.home() / ".local" / "bin"
    publication_directory(directory, root=root)
    directories = _removal_directories(slot, directory)
    _external_removal_owner(root)
    record = _application(slot)
    if record["name"] == "worktree-manager":
        raise ValueError("Manager removal requires its dedicated installation owner")
    runtime, engine = _resources(slot)
    association = _construction_inputs(slot, record, runtime, engine, deadline)
    if association.identity.plugin != component:
        raise ValueError("application removal Source differs from the requested component")
    payload_hash = association.inputs["payload_hash"]
    if not runtime.is_complete(root, slot.name, expect_hash=payload_hash):
        raise RuntimeError("application removal requires completed Source/profile construction")
    replacements = _global_replacements(slot, record, directory)
    selectors = tuple((slot.name + ending).encode("utf-8") for ending in ("\n", "\r\n"))
    observed: list[tuple[Path, bytes]] = []
    for name in ("current-version", "last-known-good"):
        path = root / name
        content = _read(path)
        if content not in selectors:
            raise RuntimeError("application removal requires the exact selected generation")
        assert content is not None
        observed.append((path, content))
    for path, content in replacements.items():
        if _read(path) != content:
            raise RuntimeError("application removal command belongs to another publication")
        observed.append((path, content))
    plan = ApplicationRemovalPlan(
        slot, root, directory, component, payload_hash, association.receipt["lock_id"],
        directories, tuple(observed),
    )
    check_deadline()
    _inspect_application_wheels(slot, association, engine, deadline)
    runtime.require_immutable_environment(slot)
    _unchanged_application(slot, association, engine)
    check_deadline()
    _removal_unchanged(plan)
    if not runtime.is_complete(root, slot.name, expect_hash=payload_hash):
        raise RuntimeError("application removal construction changed during preflight")
    return plan


@contextmanager
def application_removal_custody(
    plan: ApplicationRemovalPlan, *, deadline: float | None = None,
) -> Iterator[None]:
    """Revalidate a quiescent application under its existing runtime/publication guards.

    The application owner must quiesce its central service first. Independent
    Hosts retain their ordinary runtime leases and refuse this exclusive custody;
    this function never stops them, withdraws commands or authorizes raw deletion.
    """
    runtime, _engine = _resources(plan.slot)
    publication = runtime._runtime_component("versioned_runtime_publication")
    commands = [
        path for path, _content in plan.publications
        if path not in (plan.root / "current-version", plan.root / "last-known-good")
    ]
    with runtime._leases.batch_removal_custody(plan.root, [plan.slot.name]):
        with publication.target_leases(plan.root, commands):
            current = plan_application_removal(
                plan.slot, component=plan.component, global_bin=plan.command_directory,
                deadline=deadline,
            )
            if current != plan:
                raise RuntimeError("application removal Source/profile or ownership changed")
            yield


def _removal_packet_location(packet: Path, root: Path) -> None:
    publication_directory(packet)
    if packet.resolve().is_relative_to(root.resolve()):
        raise ValueError("application removal packet must be outside the installation")


def _copy_removal_controls(
    slot: Path, target: Path, entries: dict[str, Any], artifacts: ModuleType, deadline: float,
) -> None:
    required = {"install.py", "application.json", "runtime-source.json", "runtime", "wheels"}
    if not required <= entries.keys() or any(
        entries[name]["kind"] != ("directory" if name in {"runtime", "wheels"} else "file")
        for name in required
    ):
        raise RuntimeError("application removal controls are incomplete")
    for name, state in sorted(entries.items(), key=lambda item: (item[0].count("/"), item[0])):
        if name not in required and not name.startswith(("runtime/", "wheels/")):
            continue
        if ".." in Path(name).parts or Path(name).is_absolute():
            raise ValueError("application removal control name is invalid")
        artifacts._deadline(deadline)
        source, destination = slot / name, target / name
        if artifacts._state(source, deadline) != state:
            raise RuntimeError("application removal controls changed before retention")
        if state["kind"] == "directory":
            artifacts._native(destination).mkdir()
        elif state["kind"] == "file":
            with artifacts._native(source).open("rb") as incoming:
                with artifacts._native(destination).open("xb") as outgoing:
                    while block := incoming.read(1 << 20):
                        artifacts._deadline(deadline)
                        outgoing.write(block)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
            copied = artifacts._state(destination, deadline)
            if any(copied[key] != state[key] for key in ("kind", "size", "digest")):
                raise RuntimeError("application removal retained control differs from its original")
        else:
            raise ValueError("application removal controls must be ordinary files")
        if artifacts._state(source, deadline) != state:
            raise RuntimeError("application removal controls changed during retention")


def retain_application_removal_helper(slot: Path, helper: Path, *, deadline: float) -> None:
    """Copy only sealed controls for external preflight; never import app or retire native owners."""
    root = slot.parent.parent
    _removal_packet_location(helper, root)
    if any(Path(path).resolve().is_relative_to(root.resolve())
           for path in (sys.executable, sys.prefix)):
        raise ValueError("application removal bootstrap interpreter must be outside the installation")
    artifacts = _artifacts(slot)
    runtime, _engine = _resources(slot)
    artifacts._plain(helper.parent)
    with ExitStack() as custody:
        custody.enter_context(runtime._leases.construction_lease(root, slot.name, timeout=0))
        custody.enter_context(runtime._leases._kernel_lease(
            root, slot.name, kind="retention", shared=True, timeout=0,
        ))
        inventory = artifacts._record(slot / artifacts.INVENTORY, deadline)
        binding = inventory.get("binding")
        if not isinstance(binding, dict):
            raise ValueError("application removal original control binding is invalid")
        expected = artifacts._base(slot, binding, deadline)
        if (
            set(inventory) != {*expected, "schema", "directory", "entries"}
            or inventory["schema"] != "installer-engine.application-artifacts"
            or any(inventory[key] != value for key, value in expected.items())
            or inventory["directory"] != artifacts._state(slot, deadline)
            or not isinstance(inventory["entries"], dict)
            or not runtime.is_complete(root, slot.name, expect_hash=binding["payload_hash"])
        ):
            raise RuntimeError("application removal original controls are not sealed and complete")
        artifacts._native(helper).mkdir()
        _copy_removal_controls(slot, helper, inventory["entries"], artifacts, deadline)
        artifacts._write_once(helper / "removal-helper.json", {
            "schema": "installer-engine.application-removal-helper", "version": 1,
            "slot": str(slot), "binding": binding,
            "directory": artifacts._state(helper, deadline),
            "entries": artifacts._tree(helper, deadline),
        }, deadline)


def read_application_removal_helper(helper: Path, *, deadline: float) -> Path:
    """Read the retained original helper, never rebuilding a partial or changed copy."""
    publication_directory(helper)
    artifacts = _artifacts(Path(__file__).parent)
    record = artifacts._record(helper / "removal-helper.json", deadline)
    if (
        set(record) != {"schema", "version", "slot", "binding", "directory", "entries"}
        or record["schema"] != "installer-engine.application-removal-helper"
        or record["version"] != 1 or not isinstance(record["slot"], str)
        or not isinstance(record["binding"], dict) or not isinstance(record["entries"], dict)
    ):
        raise ValueError("application removal retained helper is invalid")
    slot = Path(record["slot"])
    artifacts._slot(slot)
    artifacts._binding(record["binding"])
    _removal_packet_location(helper, slot.parent.parent)
    current = artifacts._tree(helper, deadline)
    current.pop("removal-helper.json", None)
    if artifacts._state(helper, deadline) != record["directory"] or current != record["entries"]:
        raise RuntimeError("application removal retained helper controls changed or are incomplete")
    return slot


def prepare_application_removal(
    plan: ApplicationRemovalPlan, packet: Path, *, deadline: float,
    pending_artifacts: tuple[Path, ...] = (),
) -> None:
    """Retain original controls and receipts before any application-owned withdrawal.

    An incomplete packet is never resumed or overwritten. Its final record is
    written only after the original physical inventory and publications agree.
    """
    _removal_packet_location(packet, plan.root)
    artifacts = _artifacts(plan.slot)
    runtime, _engine = _resources(plan.slot)
    publication = runtime._runtime_component("versioned_runtime_publication")
    commands = [path for path, _content in plan.publications
                if path not in (plan.root / "current-version", plan.root / "last-known-good")]
    with ExitStack() as custody:
        custody.enter_context(runtime._leases.construction_lease(
            plan.root, plan.slot.name, timeout=0,
        ))
        custody.enter_context(runtime._leases._kernel_lease(
            plan.root, plan.slot.name, kind="retention", shared=True, timeout=0,
        ))
        custody.enter_context(runtime._leases._publication_lease(plan.root, timeout=0))
        custody.enter_context(publication.target_leases(plan.root, commands))
        if plan_application_removal(
            plan.slot, component=plan.component, global_bin=plan.command_directory,
            deadline=deadline,
        ) != plan:
            raise RuntimeError("application removal Source/profile or ownership changed")
        artifacts._deadline(deadline)
        packet.mkdir(parents=True, exist_ok=False)
        artifacts._plain(packet)
        binding = {
            "component": plan.component, "payload_hash": plan.payload_hash,
            "profile_lock_id": plan.profile_lock_id,
        }
        receipt = packet / "original-cleanup.json"
        pending = artifacts._pending(plan.slot, pending_artifacts, deadline)
        artifacts.prepare_cleanup(
            plan.slot, binding, receipt, deadline=deadline, pending_artifacts=pending_artifacts,
        )
        original = artifacts._record(receipt, deadline)
        publications = [
            {"path": str(path), "content": base64.b64encode(content).decode("ascii"),
             "state": artifacts._state(path, deadline)}
            for path, content in plan.publications
        ]
        entries = original["entries"]
        _copy_removal_controls(plan.slot, packet, entries, artifacts, deadline)
        if artifacts._tree(plan.slot, deadline, include_controls=True) != {**entries, **pending}:
            raise RuntimeError("application removal original inventory changed during retention")
        _removal_unchanged(plan)
        if any(artifacts._state(Path(row["path"]), deadline) != row["state"]
               for row in publications):
            raise RuntimeError("application removal publication identity changed during retention")
        artifacts._write_once(packet / "removal-packet.json", {
            "schema": "installer-engine.application-removal", "version": 1,
            "slot": str(plan.slot), "root": str(plan.root),
            "command_directory": str(plan.command_directory), "binding": binding,
            "directory_identities": [[str(path), device, inode]
                                     for path, device, inode in plan.directory_identities],
            "publications": publications, "pending_artifacts": pending,
            "directory": artifacts._state(packet, deadline),
            "entries": artifacts._tree(packet, deadline),
        }, deadline)


def read_application_removal(packet: Path, *, deadline: float) -> ApplicationRemovalPlan:
    """Read retained original evidence, without fresh preflight of a partial target.

    This does not acquire custody or authorize effects. Later transaction phases
    must reconcile their original evidence; absent publications cannot be adopted.
    """
    publication_directory(packet)
    artifacts = _artifacts(Path(__file__).parent)
    record = artifacts._record(packet / "removal-packet.json", deadline)
    if (
        set(record) != {
            "schema", "version", "slot", "root", "command_directory", "binding",
            "directory_identities", "publications", "pending_artifacts", "directory", "entries",
        }
        or record["schema"] != "installer-engine.application-removal" or record["version"] != 1
        or any(not isinstance(record[key], str) for key in ("slot", "root", "command_directory"))
        or not isinstance(record["binding"], dict)
        or not isinstance(record["entries"], dict)
        or not isinstance(record["pending_artifacts"], dict)
        or not isinstance(record["directory_identities"], list)
        or not isinstance(record["publications"], list)
    ):
        raise ValueError("application removal packet is invalid or incomplete")
    slot, root, commands = (Path(record[key]) for key in ("slot", "root", "command_directory"))
    _removal_packet_location(packet, root)
    if (
        not slot.is_absolute() or ".." in slot.parts or slot.parent.name != "versions"
        or slot.parent.parent != root
        or re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", slot.name) is None
        or slot.name.endswith(".")
    ):
        raise ValueError("application removal packet has an invalid original target")
    publication_directory(commands, root=root)
    artifacts._binding(record["binding"])
    if artifacts._state(packet, deadline) != record["directory"]:
        raise RuntimeError("application removal packet directory changed")
    current = artifacts._tree(packet, deadline)
    current.pop("removal-packet.json", None)
    if current != record["entries"]:
        raise RuntimeError("application removal packet controls changed or are incomplete")
    directories = record["directory_identities"]
    if (
        len(directories) != 5
        or any(not isinstance(row, list) or len(row) != 3
               or not isinstance(row[0], str)
               or any(type(value) is not int for value in row[1:]) for row in directories)
        or [Path(row[0]) for row in directories] != [
            root, slot.parent, slot, slot / ".venv", commands,
        ]
    ):
        raise ValueError("application removal packet original directories are invalid")
    publications = record["publications"]
    if (
        len(publications) < 3 or len(publications) > 1024
        or any(not isinstance(row, dict) or set(row) != {"path", "content", "state"}
               or not isinstance(row["path"], str) or not isinstance(row["content"], str)
               for row in publications)
        or len({row["path"] for row in publications}) != len(publications)
        or [Path(row["path"]) for row in publications[:2]] != [
            root / "current-version", root / "last-known-good",
        ]
        or any(Path(row["path"]).parent != commands or ".." in Path(row["path"]).parts
               for row in publications[2:])
    ):
        raise ValueError("application removal packet original publications are invalid")
    receipt = artifacts._record(packet / "original-cleanup.json", deadline)
    if (
        receipt.get("schema") != "installer-engine.application-cleanup"
        or receipt.get("slot") != str(slot) or receipt.get("binding") != record["binding"]
    ):
        raise RuntimeError("application removal packet original cleanup receipt differs")
    binding = record["binding"]
    return ApplicationRemovalPlan(
        slot, root, commands, binding["component"], binding["payload_hash"],
        binding["profile_lock_id"],
        tuple((Path(path), device, inode) for path, device, inode in directories),
        tuple((Path(row["path"]), base64.b64decode(row["content"], validate=True))
              for row in publications),
    )


def _withdrawal_evidence(
    packet: Path, receipt: Path, plan: ApplicationRemovalPlan, artifacts: ModuleType,
    deadline: float,
) -> dict[str, Any]:
    _external_removal_owner(plan.root)
    if not receipt.is_absolute() or ".." in receipt.parts:
        raise ValueError("application withdrawal receipt must be an absolute ordinary path")
    _removal_packet_location(receipt.parent, plan.root)
    if receipt.resolve().is_relative_to(packet.resolve()):
        raise ValueError("application withdrawal receipt must be outside its immutable packet")
    publication_directory(receipt.parent)
    record = artifacts._record(packet / "removal-packet.json", deadline)
    return {
        "schema": "installer-engine.application-withdrawal", "version": 1,
        "packet": str(packet),
        "packet_state": artifacts._state(packet / "removal-packet.json", deadline),
        "slot": str(plan.slot), "root": str(plan.root), "binding": record["binding"],
        "publications": record["publications"],
    }


def _withdrawal_directories(
    plan: ApplicationRemovalPlan, artifacts: ModuleType, *, partial: bool,
) -> None:
    for path, device, inode in plan.directory_identities:
        try:
            artifacts._plain(path)
            info = artifacts._native(path).lstat()
        except FileNotFoundError:
            if partial and path in (plan.slot, plan.slot / ".venv"):
                continue
            raise RuntimeError("application withdrawal original directory is missing") from None
        if (info.st_dev, info.st_ino) != (device, inode):
            raise RuntimeError("application withdrawal original directory changed")


def withdraw_application_publications(
    packet: Path, receipt: Path, *, deadline: float,
) -> None:
    """Withdraw only original publications, with durable intent before the first unlink.

    The application owner must hold service-start exclusion and first settle its
    own startup/connector withdrawal. This helper never retries a native operation.
    """
    plan = read_application_removal(packet, deadline=deadline)
    artifacts = _artifacts(packet)
    evidence = _withdrawal_evidence(packet, receipt, plan, artifacts, deadline)
    runtime, _engine = _resources(packet)
    publication = runtime._runtime_component("versioned_runtime_publication")
    commands = [path for path, _content in plan.publications
                if path not in (plan.root / "current-version", plan.root / "last-known-good")]
    with runtime._leases.batch_removal_custody(plan.root, [plan.slot.name]):
        with publication.target_leases(plan.root, commands):
            partial = os.path.lexists(artifacts._native(receipt))
            if partial and artifacts._record(receipt, deadline) != evidence:
                raise RuntimeError("application withdrawal original receipt changed")
            _withdrawal_directories(plan, artifacts, partial=partial)
            remaining = []
            for row, (path, content) in zip(evidence["publications"], plan.publications):
                if not os.path.lexists(artifacts._native(path)):
                    if not partial:
                        raise RuntimeError("application publication vanished without original intent")
                    continue
                if artifacts._state(path, deadline) != row["state"] or _read(path) != content:
                    raise RuntimeError("application withdrawal publication belongs to a changed owner")
                remaining.append((path, row["state"]))
            if not partial:
                receipt.parent.mkdir(parents=True, exist_ok=True)
                artifacts._write_once(receipt, evidence, deadline)
            for path, original in remaining:
                artifacts._deadline(deadline)
                if artifacts._state(path, deadline) != original:
                    raise RuntimeError("application publication changed before withdrawal")
                artifacts._native(path).unlink()
                artifacts._deadline(deadline)


def reclaim_application_removal(
    packet: Path, receipt: Path, *, deadline: float,
    validate: Callable[[], None] | None = None,
) -> None:
    """Reclaim the original finite inventory through canonical runtime custody.

    Publication guards from withdrawal must already be released. The application's
    service-start exclusion remains its caller's responsibility throughout.
    Additional application references are checked inside canonical runtime
    custody, immediately before the finite receipted cleanup.
    """
    plan = read_application_removal(packet, deadline=deadline)
    artifacts = _artifacts(packet)
    evidence = _withdrawal_evidence(packet, receipt, plan, artifacts, deadline)
    if artifacts._record(receipt, deadline) != evidence:
        raise RuntimeError("application reclamation requires its original withdrawal receipt")
    _withdrawal_directories(plan, artifacts, partial=True)
    if any(os.path.lexists(artifacts._native(path)) for path, _content in plan.publications):
        raise RuntimeError("application reclamation publications are not withdrawn")
    runtime, _engine = _resources(packet)

    def remove(candidate: Path) -> None:
        if artifacts._native(candidate) != artifacts._native(plan.slot):
            raise RuntimeError("application reclamation selected a different candidate")
        if validate is not None:
            validate()
        artifacts._deadline(deadline)
        artifacts.remove_from_receipt(
            candidate, evidence["binding"], packet / "original-cleanup.json", deadline=deadline,
        )

    runtime.remove_owned_candidate(plan.root, plan.slot.name, remove=remove)


def _resources(slot: Path) -> tuple[ModuleType, ModuleType]:
    runtime_path = slot / "runtime"
    runtime = _load(runtime_path / "versioned_runtime.py", "versioned_runtime")
    previous = sys.path[:]
    try:
        sys.path.insert(0, str(runtime_path))
        engine = _load(runtime_path / "installer_engine.py", "_application_engine")
    finally:
        sys.path[:] = previous
    return runtime, engine


def _artifacts(slot: Path) -> ModuleType:
    directory = slot / "runtime"
    previous = sys.path[:]
    try:
        sys.path.insert(0, str(directory))
        return _load(directory / "runtime_application_artifacts.py", "_application_artifacts")
    finally:
        sys.path[:] = previous


def _construction_metadata(
    record: dict[str, Any], runtime: ModuleType, engine: ModuleType, deadline: float,
) -> SimpleNamespace:
    """Validate claimed Source/profile facts without reading or qualifying a slot."""
    return cast(SimpleNamespace, _qualification()._construction_metadata(
        sys.modules[__name__], record, runtime, engine, deadline,
    ))


def _construction_inputs(
    slot: Path, record: dict[str, Any], runtime: ModuleType, engine: ModuleType, deadline: float,
) -> SimpleNamespace:
    return cast(SimpleNamespace, _qualification()._construction_inputs(
        sys.modules[__name__], slot, record, runtime, engine, deadline,
    ))


def _unchanged_application(slot: Path, association: SimpleNamespace, engine: ModuleType) -> None:
    _qualification()._unchanged_application(sys.modules[__name__], slot, association, engine)


def _inspect_application_wheels(
    slot: Path, association: SimpleNamespace, engine: ModuleType, deadline: float,
) -> None:
    _qualification()._inspect_application_wheels(
        sys.modules[__name__], slot, association, engine, deadline,
    )


def qualify_installed_application(
    slot: Path, *, component: str, command: str, entry_point: str,
    deadline: float | None = None,
) -> InstalledApplicationSource:
    """Read selected completed Source/profile evidence without lifecycle effects."""
    return cast(InstalledApplicationSource, _qualification().qualify_installed_application(
        sys.modules[__name__], slot, component=component, command=command,
        entry_point=entry_point, deadline=deadline,
    ))


@contextmanager
def _qualified(
    slot: Path, record: dict[str, Any], *, verify_on_exit: bool = True,
) -> Iterator[tuple[ModuleType, Callable[[], None]]]:
    with _qualification()._qualified(sys.modules[__name__], slot, record, verify_on_exit=verify_on_exit) as result:
        yield result


def _run(
    record: dict[str, Any], command: list[str], root: Path, slot: Path, *, timeout: float = 180,
) -> None:
    _qualification()._run(sys.modules[__name__], record, command, root, slot, timeout=timeout)


def _global_replacements(
    slot: Path, record: dict[str, Any], global_bin: Path | None,
) -> dict[Path, bytes]:
    return cast(dict[Path, bytes], _qualification()._global_replacements(
        sys.modules[__name__], slot, record, global_bin,
    ))


def _publication_current(
    slot: Path, record: dict[str, Any], *, global_bin: Path | None = None,
) -> bool:
    """Verify publication only; the caller retains and health-gates the candidate."""
    return cast(bool, _qualification()._publication_current(
        sys.modules[__name__], slot, record, global_bin=global_bin,
    ))


def install(
    slot: Path, *, stage: bool, global_bin: Path | None = None,
    arguments: tuple[str, ...] = (),
) -> dict[str, str]:
    """UV has synchronized the environment before this entry runs."""
    if global_bin is not None and stage:
        raise ValueError("application command directory cannot be selected with --stage")
    slot = slot.resolve()
    if global_bin is not None:
        publication_directory(global_bin, root=slot.parent.parent)
    if (
        not (slot / ".venv" / "pyvenv.cfg").is_file()
        or not Path(sys.prefix).samefile(slot / ".venv")
    ):
            raise RuntimeError(
                "application install must run through its slot-local uv binstub "
                f"(prefix={Path(sys.prefix).resolve()}, expected={(slot / '.venv').resolve()}, "
                f"config={(slot / '.venv' / 'pyvenv.cfg').is_file()})"
            )
    _materialize_interpreter(slot)
    if stage:
        return {"state": "staged", "slot": str(slot)}
    if slot.parent.name != "versions" or not re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", slot.name):
        raise ValueError("regular install requires an owned version-slot layout")
    root = slot.parent.parent
    record = _application(slot)
    budgets = callback_timeouts(record)
    if record["name"] == "worktree-manager":
        if arguments:
            raise ValueError("Manager installation does not accept application configuration arguments")
        if global_bin is not None and global_bin != Path.home() / ".local" / "bin":
            raise ValueError("Manager installation requires its owned global command directory")
        _run(record, ["self-install", "--apply"], root, slot, timeout=budgets["start"])
        return {"state": "installed", "slot": str(slot)}
    global_bin = global_bin or Path.home() / ".local" / "bin"
    replacements = _global_replacements(slot, record, global_bin)
    previous = {path: _read(path) for path in replacements}
    selectors = (root / "current-version", root / "last-known-good")
    if arguments and not (record["setup"] or record["start"]):
        raise ValueError("application installation has no configurable setup or startup action")
    setup = [*record["setup"], *arguments] if record["setup"] else []
    start = [*record["start"], *arguments] if record["start"] else []
    with _qualified(
        slot, record, verify_on_exit=False,
    ) as (runtime, unchanged):
        if (
            not arguments and not setup and not start
            and _publication_current(slot, record, global_bin=global_bin)
        ):
            unchanged()
            return {"state": "installed", "slot": str(slot)}
        _run(record, setup, root, slot, timeout=budgets["setup"])
        unchanged()
        if not runtime.is_complete(root, slot.name, expect_hash=record["construction"]["payload_hash"]):
            raise RuntimeError("application publication requires canonical completed construction")
        selector_content = (slot.name + "\n").encode("utf-8")
        selector_forms = (selector_content, (slot.name + "\r\n").encode("utf-8"))
        success_content = json.dumps({
            "state": "installed", "version": slot.name,
        }, sort_keys=True).encode("utf-8") + b"\n"
        publication = runtime._runtime_component("versioned_runtime_publication")
        with runtime._publication_lease(root), publication.target_leases(root, list(replacements)):
            selector_before = {path: _read(path) for path in selectors}
            written = {}
            publishing = False
            try:
                for path, content in replacements.items():
                    if _read(path) != previous[path]:
                        raise RuntimeError(f"application launcher changed before publication: {path}")
                    if previous[path] != content:
                        _write(path, content)
                        written[path] = content
                publishing = True
                publication.publish_selectors(
                    root, slot.name, None, lambda home, version: None,
                )
                if not start:
                    _write(root / "last-known-good", selector_content)
                    _write(slot / "install-success.json", success_content)
            except BaseException as error:
                if publishing and any(
                    _read(path) not in (selector_before[path], selector_content)
                    for path in selectors
                ):
                    raise RuntimeError("application selector changed during recovery") from error
                for path, content in written.items():
                    if _read(path) != content:
                        raise RuntimeError(f"application publication changed during recovery: {path}")
                    original = previous[path]
                    if original is None:
                        path.unlink()
                    else:
                        _write(path, original)
                if publishing:
                    for path in selectors:
                        selected_before = selector_before[path]
                        observed = _read(path)
                        if observed == selected_before:
                            continue
                        if observed != selector_content:
                            raise RuntimeError(f"application selector changed during recovery: {path}")
                        if selected_before is None:
                            path.unlink(missing_ok=True)
                        else:
                            _write(path, selected_before)
                raise
        if not start:
            return {"state": "installed", "slot": str(slot)}
        try:
            _run(record, start, root, slot, timeout=budgets["start"])
            unchanged()
            with runtime._publication_lease(root), publication.target_leases(root, list(replacements)):
                if (
                    _read(selectors[0]) not in selector_forms
                    or _read(selectors[1]) not in (selector_before[selectors[1]], *selector_forms)
                ):
                    raise RuntimeError("application selector changed before startup completion")
                if any(_read(path) != content for path, content in replacements.items()):
                    raise RuntimeError("application launcher changed before startup completion")
                _write(root / "last-known-good", selector_content)
                _write(slot / "install-success.json", success_content)
        except BaseException as error:
            uncertain = ApplicationStartupUncertain(slot)
            if isinstance(error, Exception):
                raise uncertain from error
            print(str(uncertain), file=sys.stderr)
            raise
    return {"state": "installed", "slot": str(slot)}


def run_application_removal(
    slot: Path, workspace: Path, *, arguments: list[str], deadline: float,
    global_bin: Path | None = None,
) -> Any:
    """Reach the app's original callback through sealed external controls, never its venv."""
    publication_directory(workspace)
    packet, helper = workspace / "packet", workspace / "helper"
    if os.path.lexists(packet):
        original = read_application_removal(packet, deadline=deadline)
        controls, original_slot = packet, original.slot
    elif os.path.lexists(helper):
        controls, original_slot = helper, read_application_removal_helper(helper, deadline=deadline)
    else:
        removal_callback(_application(slot).get("remove"))
        _removal_packet_location(workspace, slot.parent.parent)
        if os.path.lexists(workspace):
            raise RuntimeError("application removal workspace is partial; retain its original evidence")
        workspace.mkdir()
        retain_application_removal_helper(slot, helper, deadline=deadline)
        controls, original_slot = helper, slot
    artifacts = _artifacts(controls)
    if (
        artifacts._native(slot) not in {
            artifacts._native(original_slot), artifacts._native(controls),
        }
    ):
        raise ValueError("application removal workspace belongs to another original slot")
    _removal_packet_location(workspace, original_slot.parent.parent)
    if slot == controls:
        _external_removal_owner(original_slot.parent.parent)
    if any(Path(path).resolve().is_relative_to(original_slot.parent.parent.resolve())
           for path in (sys.executable, sys.prefix)):
        raise ValueError("application removal interpreter must be outside the installation")
    provider = _load(controls / "install.py", "_external_application_removal_provider")
    if controls == packet:
        provider.read_application_removal(packet, deadline=deadline)
    else:
        provider.read_application_removal_helper(helper, deadline=deadline)
    callback = removal_callback(provider._application(controls).get("remove"))
    module, attribute = callback.split(":")
    wheels = controls / "wheels"
    original_path = list(sys.path)
    try:
        sys.path[:] = [
            str(path) for path in sorted(wheels.glob("*.whl"))
        ] + [
            path for path in original_path
            if not {"site-packages", "dist-packages"} & set(Path(path).parts)
            and path and not Path(path).resolve().is_relative_to(original_slot.parent.parent.resolve())
        ]
        owner = importlib.import_module(module)
        if not Path(owner.__file__ or "").is_relative_to(wheels):
            raise RuntimeError("application removal callback was not loaded from its retained wheels")
        command: Any = owner
        for part in attribute.split("."):
            command = getattr(command, part)
        if not callable(command):
            raise ValueError("application removal callback is not callable")
        return command(
            provider=provider, slot=original_slot, workspace=workspace,
            arguments=arguments, global_bin=global_bin, deadline=deadline,
        )
    finally:
        sys.path[:] = original_path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["uninstall"]:
        removal = argparse.ArgumentParser(allow_abbrev=False)
        removal.add_argument("--workspace", type=Path, required=True)
        removal.add_argument("--global-bin", type=Path)
        removal.add_argument("--timeout", type=float, default=60)
        options, forwarded = removal.parse_known_args(argv[1:])
        if not math.isfinite(options.timeout) or not 0 < options.timeout <= 600:
            removal.error("--timeout must be finite seconds in (0, 600]")
        print(json.dumps(run_application_removal(
            Path(__file__).parent, options.workspace, arguments=forwarded,
            global_bin=options.global_bin, deadline=time.monotonic() + options.timeout,
        )))
        return 0
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--global-bin", type=Path, help="command publication directory (regular install)")
    parser.add_argument(
        "arguments", nargs=argparse.REMAINDER,
        help="application-owned setup/start arguments after -- (regular installation only)",
    )
    arguments = parser.parse_args(argv)
    if arguments.stage and arguments.global_bin is not None:
        parser.error("--global-bin cannot be selected with --stage")
    forwarded = arguments.arguments
    if forwarded[:1] == ["--"]:
        forwarded = forwarded[1:]
    print(json.dumps(install(
        Path(__file__).parent, stage=arguments.stage, arguments=tuple(forwarded),
        **({"global_bin": arguments.global_bin} if arguments.global_bin is not None else {}),
    )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
