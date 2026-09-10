#!/usr/bin/env python3
"""Explicit, receipt-authorized Agent Machines attribution, deactivation, repair, and uninstall."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import stat
import sys
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
SCRIPT_ROOT = Path(__file__).resolve().parent
PAYLOAD_MANIFEST = SCRIPT_ROOT.parent / "payload-invocation.json"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load lifecycle dependency: {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ic = _load("agent_machines_installation_context", SCRIPT_ROOT / "installation-context" / "installation_context.py")
vr = _load("agent_machines_versioned_runtime", SCRIPT_ROOT / "versioned_runtime.py")


class RevalidationRequired(Exception):
    """A caller's generation, selection, or captured artifact has changed."""


@contextmanager
def provisioning_lock(root: Path):
    """Join the installer's outer lock without trusting its inherited flag."""
    path = root / ".payload-provision.lock"
    ic._path_evidence(path)
    deadline = time.monotonic() + 30
    descriptor = -1
    while descriptor < 0:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    try:
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            # The shell's no-flock fallback is deliberately not reclaimed here.
            if os.path.lexists(root / ".payload-provision.lock.pid"):
                ic._fail("A fallback provisioning owner must finish before lifecycle management.")
        if ic._stat_identity(os.fstat(descriptor)) != ic._stat_identity(path.lstat()):
            ic._fail("Provisioning lock was replaced.")
        yield
    finally:
        os.close(descriptor)


def _tree(root: Path, *, runtime: bool = False) -> dict[Path, tuple]:
    """Capture a bounded no-follow deletion plan, including directory identities."""
    result = {}
    pending = [root]
    while pending:
        path = pending.pop()
        if len(result) >= ic.MAX_SNAPSHOT_ENTRIES:
            ic._fail("Lifecycle artifact tree exceeds the entry limit.")
        if ic._is_link_or_junction(path):
            relative = path.relative_to(root).as_posix()
            allowed = runtime and os.name != "nt" and (
                relative in {"bin/python", "bin/python3", f"bin/python{sys.version_info.major}.{sys.version_info.minor}"}
                or (relative.startswith("bin/python3.") and relative[12:].isdigit())
                or (relative == "lib64" and os.readlink(path) == "lib")
            )
            if not allowed or not (root / "pyvenv.cfg").is_file() or ic._is_link_or_junction(root / "pyvenv.cfg"):
                ic._fail(f"Uninstall refuses linked or reparsed artifact: {path.name}")
            if relative != "lib64" and not path.resolve(strict=True).is_file():
                ic._fail("Runtime interpreter link must resolve to a regular file.")
            info = path.lstat()
            result[path] = (ic._stat_identity(info), ic._stat_metadata(info), os.readlink(path))
        else:
            result[path] = ic._path_evidence(path)
            if result[path] is None:
                raise RevalidationRequired("artifact-disappeared")
            if path.is_dir():
                pending.extend(path.iterdir())
    return result


def _interpreter(slot: Path) -> Path:
    path = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not path.is_file() or not (slot / "pyvenv.cfg").is_file():
        ic._fail("Repair requires an existing completed runtime interpreter and pyvenv.cfg.")
    if ic._is_link_or_junction(slot / "pyvenv.cfg") or (os.name == "nt" and ic._is_link_or_junction(path)):
        ic._fail("Runtime interpreter evidence is linked or reparsed.")
    if ic._is_link_or_junction(path.parent):
        ic._fail("Runtime interpreter directory is linked or reparsed.")
    return path


def _manifest_path_matches(value: Any, expected: Path | str) -> bool:
    # Preserve lexical slot selection: resolving bin/python would also accept
    # its external base interpreter instead of the owned invocation path.
    return (
        isinstance(value, str) and ic._path_is_fully_qualified(value)
        and ic._file_cache_key(Path(value)) == ic._file_cache_key(Path(expected))
    )


def _legacy_path_items(profile: Path) -> tuple[Path, list[dict[str, str]]]:
    manifest = ic.read_json(PAYLOAD_MANIFEST)
    if not isinstance(manifest, dict):
        ic._fail("payload-invocation.json must be a JSON object.")
    installation = ic._property(manifest, "installation")
    if not isinstance(installation, dict):
        ic._fail("payload-invocation.json installation must be a JSON object.")
    footprint = ic._property(installation, "legacyFootprint")
    if not isinstance(footprint, dict):
        ic._fail("payload-invocation.json legacyFootprint must be a JSON object.")
    paths = ic._property(footprint, "paths")
    if not isinstance(paths, list):
        ic._fail("payload-invocation.json legacyFootprint.paths must be an array.")
    items: list[dict[str, str]] = []
    legacy_root: Path | None = None
    for entry in paths:
        if not isinstance(entry, str) or not entry:
            ic._fail("legacyFootprint.paths entries must be non-empty strings.")
        raw = Path(entry)
        absolute = raw if ic._path_is_fully_qualified(raw) else profile / raw
        record = {
            "kind": "path",
            "identity": entry,
            "path": str(Path(os.path.abspath(os.fspath(absolute)))),
        }
        items.append(record)
        if entry == ".agent-machines":
            legacy_root = Path(record["path"])
    if legacy_root is None:
        ic._fail("payload-invocation.json must declare .agent-machines in legacyFootprint.paths.")
    return legacy_root, items


def _attribute_legacy(arguments: argparse.Namespace) -> dict[str, Any]:
    if not ic._path_is_fully_qualified(arguments.context) or not ic._path_is_fully_qualified(arguments.durable_home):
        ic._fail("Legacy attribution requires explicit absolute context and durable home.")
    durable = ic.canonical_path(arguments.durable_home)
    current_environment, profile = ic._current_environment(
        environment=os.environ,
        os_profile=None,
        platform=None,
        wsl_distro=None,
    )
    legacy_root, items = _legacy_path_items(profile)
    present = [
        {
            **item,
            "classification": "orphaned",
            "reason": "legacy-root-unavailable",
        }
        for item in items
        if os.path.lexists(Path(item["path"]))
    ]
    absent = [
        {
            **item,
            "classification": "absent",
            "reason": "legacy-absent",
        }
        for item in items
        if not os.path.lexists(Path(item["path"]))
    ]
    if not legacy_root.is_dir() or ic._is_link_or_junction(legacy_root):
        return {
            "action": "attribute-legacy",
            "status": "preserved" if present else "ready",
            "reason": "legacy-root-unavailable" if present else "no-legacy-state",
            "legacyRoot": str(legacy_root),
            "context": str(ic.canonical_path(arguments.context)),
            "tombstone": None,
            "tombstoneChanged": False,
            "activation": None,
            "activationChanged": False,
            "activationGeneration": 0,
            "namespaceGeneration": None,
            "installGeneration": None,
            "attributedItems": [],
            "preservedItems": present,
            "absentItems": absent,
            "operative": False,
        }
    return ic.attribute_legacy_state(
        context=arguments.context,
        expected_marketplace_id=arguments.expected_marketplace_id,
        expected_plugin_id="agent-machines",
        legacy_root=legacy_root,
        legacy_items=items,
        legacy_lock=provisioning_lock(legacy_root),
        durable_home=durable,
        maintenance_token=getattr(arguments, "maintenance_token", None),
        environment=os.environ,
        os_profile=profile,
        platform=current_environment["platform"],
        wsl_distro=current_environment["wslDistro"],
    )


def _deactivate(arguments: argparse.Namespace) -> dict[str, Any]:
    if (
        not ic._path_is_fully_qualified(arguments.context)
        or not ic._path_is_fully_qualified(arguments.durable_home)
    ):
        ic._fail("Cell deactivation requires explicit absolute context and durable home.")
    durable = ic.canonical_path(arguments.durable_home)
    current_environment, profile = ic._current_environment(
        environment=os.environ,
        os_profile=None,
        platform=None,
        wsl_distro=None,
    )
    legacy_root, items = _legacy_path_items(profile)
    present = [item for item in items if os.path.lexists(Path(item["path"]))]
    legacy_probe = {
        "declared": True,
        "result": "present" if present else "absent",
        "checkedAt": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    if arguments.expected_tombstone_activation_generation is not None:
        if not legacy_root.is_dir() or ic._is_link_or_junction(legacy_root):
            ic._fail(
                "Legacy attribution rollback requires an existing unlinked legacy root."
            )
        legacy_lock = provisioning_lock(legacy_root)
    elif legacy_root.is_dir() and not ic._is_link_or_junction(legacy_root):
        legacy_lock = provisioning_lock(legacy_root)
    else:
        legacy_lock = nullcontext()
    return ic.deactivate_installation(
        context=arguments.context,
        expected_marketplace_id=arguments.expected_marketplace_id,
        expected_plugin_id="agent-machines",
        expected_namespace_generation=arguments.expected_namespace_generation,
        expected_install_generation=arguments.expected_install_generation,
        expected_activation_generation=arguments.expected_activation_generation,
        legacy_root=legacy_root,
        legacy_probe=legacy_probe,
        expected_tombstone_activation_generation=getattr(
            arguments,
            "expected_tombstone_activation_generation",
            None,
        ),
        expect_tombstone_absent=getattr(arguments, "expect_tombstone_absent", False),
        legacy_lock=legacy_lock,
        durable_home=durable,
        maintenance_token=getattr(arguments, "maintenance_token", None),
        environment=os.environ,
        os_profile=profile,
        platform=current_environment["platform"],
        wsl_distro=current_environment["wslDistro"],
    )


def lifecycle(arguments: argparse.Namespace) -> dict[str, Any]:
    """Execute a derived-only repair or a state-preserving owned uninstall."""
    a = arguments
    if a.action == "cell-attribute-legacy":
        return _attribute_legacy(a)
    if a.action == "cell-deactivate":
        return _deactivate(a)
    if not ic._path_is_fully_qualified(a.context) or not ic._path_is_fully_qualified(a.durable_home):
        ic._fail("Lifecycle management requires explicit absolute context and durable home.")
    durable = ic.canonical_path(a.durable_home)
    if not ic._path_is_fully_qualified(a.expected_payload_root):
        ic._fail("Expected payload root must be absolute.")
    if not a.expected_payload_version.strip():
        ic._fail("Expected payload version must be a non-empty string.")
    ic._assert_runtime_version(a.runtime_version)
    ic._assert_snapshot_id(a.snapshot_id)
    for expected in (a.expected_current_version, a.expected_last_known_good_version):
        if expected is not None:
            ic._assert_runtime_version(expected)

    def validate():
        return ic.validate_context_receipt(
            a.context, durable, expected_marketplace_id=a.expected_marketplace_id,
            expected_plugin_id="agent-machines", environment={},
        )

    initial = validate()
    root = Path(initial["pluginRoot"])
    removed: list[str] = []
    result = {
        "schema": "copilot-extensions.agent-machines-lifecycle", "version": 1,
        "action": a.action, "marketplaceId": a.expected_marketplace_id,
        "pluginId": "agent-machines", "context": str(root / "install.json"),
        "status": "ready", "reason": "", "changed": False, "removed": removed,
        "statePreserved": True,
    }
    with provisioning_lock(root):
        locks = (
            ic._DirectoryLock(
                durable / "marketplaces" / ".locks" / f"{a.expected_marketplace_id}.genesis",
                kind="genesis", marketplace_id=a.expected_marketplace_id, timeout_seconds=30,
            ),
            ic._DirectoryLock(
                Path(initial["cellRoot"]) / ".locks" / "agent-machines.install.lock",
                kind="install", marketplace_id=a.expected_marketplace_id,
                plugin_id="agent-machines", timeout_seconds=30,
            ),
        )
        with locks[0], locks[1]:
            validated = validate()
            receipts = {Path(validated[key]): ic._path_evidence(Path(validated[key]))
                        for key in ("namespaceReceipt", "installReceipt")}
            # This adapter owns only the canonical Agent Machines layout.
            for name in ic.ROOT_NAMES:
                if not ic.paths_equal(validated[f"{name}Root"], root / name):
                    ic._fail("Agent Machines lifecycle requires its canonical root layout.")
            marker_paths = [root / ic.CURRENT_VERSION_FILE, root / ic.LAST_KNOWN_GOOD_FILE]
            marker_values = [a.expected_current_version, a.expected_last_known_good_version]
            marker_evidence = [ic._path_evidence(path) for path in marker_paths]
            activation_path = root / "installation-activation.json"
            activation_evidence = ic._path_evidence(activation_path)
            current_environment, profile = ic._current_environment(
                environment=os.environ, os_profile=None, platform=None, wsl_distro=None
            )
            maintenance = [root / "maintenance", profile / ".copilot-extensions" / "maintenance"]
            guards: dict[Path, tuple] = {}
            anchor_identities = {
                path: ic._stat_identity(path.lstat())
                for path in (root, root.parent, Path(validated["cellRoot"]), root / "versions", root / "snapshots")
                if path.exists()
            }
            inventory: dict[Path, set[str]] = {}

            def revalidate():
                current = validate()
                if current["namespaceGeneration"] != a.expected_namespace_generation or current["generation"] != a.expected_install_generation:
                    raise RevalidationRequired("generation-changed")
                if current != validated or any(ic._path_evidence(path) != value for path, value in receipts.items()):
                    raise RevalidationRequired("receipt-changed")
                for path, identity in anchor_identities.items():
                    if ic._is_link_or_junction(path) or not path.is_dir() or ic._stat_identity(path.lstat()) != identity:
                        raise RevalidationRequired("installation-directory-changed")
                for path, names in inventory.items():
                    actual_names = {p.name for p in path.iterdir()} if path.exists() else set()
                    if actual_names != names:
                        raise RevalidationRequired("installation-inventory-changed")
                for path in maintenance:
                    if os.path.lexists(path):
                        ic.require_management_authorization(
                            profile=profile,
                            plugin_root=root,
                            maintenance_token=getattr(a, "maintenance_token", None),
                            current_time=datetime.now(timezone.utc),
                            host=socket.gethostname(),
                            pid_is_live=ic._pid_is_live,
                        )
                        break
                if ic._path_evidence(activation_path) != activation_evidence:
                    raise RevalidationRequired("activation-changed")
                if activation_evidence is not None:
                    activation = ic._activation_result(
                        plugin_root=root, durable_home=durable, marketplace_id=a.expected_marketplace_id,
                        plugin_id="agent-machines", current_environment=current_environment,
                        legacy_root=profile / ".agent-machines",
                    )
                    if activation["state"] != "valid":
                        ic._fail("Lifecycle management requires valid current activation evidence.")
                    if a.action == "cell-uninstall" and activation["actualMode"] != "legacy":
                        ic._fail("Deactivate the installation explicitly before uninstall.")
                for i, path in enumerate(marker_paths):
                    if ic._read_runtime_marker(path, path.name) != marker_values[i] or ic._path_evidence(path) != marker_evidence[i]:
                        raise RevalidationRequired("selection-changed")
                for path, evidence in guards.items():
                    if ic._path_evidence(path) != evidence:
                        raise RevalidationRequired("ownership-evidence-changed")
                for lock in locks:
                    lock.assert_owned()

            def completion(version, snapshot_id, payload_root, payload_version):
                return ic.validate_runtime_slot_completion(
                    context=a.context, expected_marketplace_id=a.expected_marketplace_id,
                    expected_plugin_id="agent-machines", expected_payload_root=payload_root,
                    expected_payload_version=payload_version, snapshot_id=snapshot_id,
                    runtime_version=version, durable_home=durable, environment={},
                )

            try:
                revalidate()
                slot = root / "versions" / a.runtime_version
                if a.action == "cell-repair":
                    completed = completion(a.runtime_version, a.snapshot_id, a.expected_payload_root, a.expected_payload_version)
                    interpreter = _interpreter(slot)
                    for path in (
                        slot / ic.RUNTIME_SLOT_OWNERSHIP_FILE,
                        slot / ic.RUNTIME_SLOT_COMPLETION_FILE,
                        root / "snapshots" / a.snapshot_id / ic.SNAPSHOT_PROVENANCE_FILE,
                        interpreter.parent, slot / "pyvenv.cfg",
                    ):
                        guards[path] = ic._path_evidence(path)
                    evidence = ic.read_json(completed["completion"])
                    snapshot = ic.read_json(root / "snapshots" / a.snapshot_id / ic.SNAPSHOT_PROVENANCE_FILE)
                    payload = ic.read_json(root / "install.json")["payload"]
                    kind = lambda p: "marketplace" if p["origin"] == "installed" else "local"
                    manifest = {
                        "schema_version": 4, "service": "agent-machines",
                        "deployed_at": evidence["completedAt"], "deployed_by": "installation-context",
                        "source": {"kind": kind(payload), "path": Path(payload["root"]).as_posix(), "repo": "copilot-extensions",
                                   "plugin": "agent-machines", "version": payload["version"],
                                   "commit": None, "branch": None, "dirty": False},
                        "runtime": {"kind": "python", "version": a.runtime_version, "path": slot.as_posix(),
                                    "interpreter": interpreter.as_posix(),
                                    "selectedBy": {"kind": kind(snapshot["payload"]), "path": Path(snapshot["payload"]["root"]).as_posix(),
                                                   "version": a.expected_payload_version}},
                        "installation": {"marketplaceId": a.expected_marketplace_id, "pluginId": "agent-machines", "context": (root / "install.json").as_posix()},
                    }
                    manifest_path = root / "deploy-manifest.json"
                    observed_manifest = ic._path_evidence(manifest_path)
                    desired = ic._json_bytes(manifest)
                    for i, path in enumerate(marker_paths):
                        if marker_values[i] != a.runtime_version:
                            revalidate()
                            completion(a.runtime_version, a.snapshot_id, a.expected_payload_root, a.expected_payload_version)
                            ic._atomic_write_text(path, a.runtime_version, lock=locks)
                            marker_values[i] = a.runtime_version
                            marker_evidence[i] = ic._path_evidence(path)
                            result["changed"] = True
                    revalidate()
                    completion(a.runtime_version, a.snapshot_id, a.expected_payload_root, a.expected_payload_version)
                    if ic._path_evidence(manifest_path) != observed_manifest:
                        raise RevalidationRequired("deploy-manifest-changed")
                    if observed_manifest is None or manifest_path.read_bytes() != desired:
                        ic._atomic_write_json(manifest_path, manifest, lock=locks)
                        result["changed"] = True
                    result["reason"] = "cell-repaired" if result["changed"] else "cell-repair-healthy"
                    return result

                allowed = {"install.json", "installation-activation.json", ".payload-provision.lock",
                           ic.DEACTIVATION_RECORDS_DIR,
                           "current-version", "last-known-good", "deploy-manifest.json", *ic.ROOT_NAMES}
                if any(path.name not in allowed for path in root.iterdir()):
                    ic._fail("Uninstall refuses unrecognized plugin-root artifacts.")
                inventory = {
                    path: {p.name for p in path.iterdir()} if path.exists() else set()
                    for path in (root, root / "versions", root / "snapshots")
                }
                manifest_path = root / "deploy-manifest.json"
                if os.path.lexists(manifest_path):
                    ic._path_evidence(manifest_path)
                    manifest = ic.read_json(manifest_path)
                    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 4:
                        ic._fail("Uninstall refuses malformed deploy manifest.")
                    installation = manifest.get("installation")
                    runtime = manifest.get("runtime")
                    source = manifest.get("source")
                    payload = ic.read_json(root / "install.json")["payload"]
                    if (manifest.get("service") != "agent-machines" or not isinstance(installation, dict)
                            or set(installation) != {"marketplaceId", "pluginId", "context"}
                            or installation["marketplaceId"] != a.expected_marketplace_id or installation["pluginId"] != "agent-machines"
                            or not _manifest_path_matches(installation["context"], root / "install.json")
                            or not isinstance(runtime, dict) or not isinstance(source, dict)
                            or not _manifest_path_matches(source.get("path"), payload["root"]) or source.get("version") != payload["version"]
                            or source.get("plugin") != "agent-machines" or source.get("repo") != "copilot-extensions"):
                        ic._fail("Uninstall refuses foreign or unattributable deploy manifest.")
                    if (source.get("kind") not in ("local", "marketplace")
                            or type(source.get("dirty")) is not bool
                            or any(key not in source or (source[key] is not None and not isinstance(source[key], str))
                                   for key in ("commit", "branch"))):
                        ic._fail("Uninstall refuses malformed deploy source evidence.")
                    version = runtime.get("version")
                    ic._assert_runtime_version(version)
                    manifest_slot = root / "versions" / version
                    if (runtime.get("kind") != "python" or not _manifest_path_matches(runtime.get("path"), manifest_slot)
                            or not _manifest_path_matches(runtime.get("interpreter"), _interpreter(manifest_slot))
                            or (marker_values[0] is not None and version != marker_values[0])):
                        ic._fail("Uninstall deploy manifest does not match runtime selection.")
                    selected_by = runtime.get("selectedBy")
                    if (not isinstance(selected_by, dict)
                            or selected_by.get("kind") not in ("local", "marketplace")
                            or not isinstance(selected_by.get("path"), str)
                            or not isinstance(selected_by.get("version"), str)):
                        ic._fail("Uninstall refuses malformed selecting-payload evidence.")
                    owner = ic.read_json(manifest_slot / ic.RUNTIME_SLOT_OWNERSHIP_FILE)
                    selected_snapshot = ic._string_property(ic._property(owner, "snapshot"), "id")
                    completion(version, selected_snapshot, selected_by["path"], selected_by["version"])
                    guards[manifest_path] = ic._path_evidence(manifest_path)
                plans: list[tuple[Path, dict[Path, tuple], list[Path]]] = []
                snapshots: dict[str, dict] = {}
                for snapshots_root in (root / "snapshots",):
                    ic._path_evidence(snapshots_root)
                    if snapshots_root.exists():
                        for path in sorted(snapshots_root.iterdir()):
                            proven = ic._validate_snapshot_provenance(
                                context=a.context, expected_marketplace_id=a.expected_marketplace_id,
                                expected_plugin_id="agent-machines", snapshot_id=path.name,
                                durable_home=durable, environment={}, require_current_receipts=False,
                            )
                            receipt = path / ic.SNAPSHOT_PROVENANCE_FILE
                            snapshots[path.name] = proven
                            guards[receipt] = ic._path_evidence(receipt)
                versions = root / "versions"
                ic._path_evidence(versions)
                if versions.exists():
                    for path in sorted(versions.iterdir()):
                        ic._assert_runtime_version(path.name)
                        if os.path.lexists(path / ic.RUNTIME_SLOT_RESERVATION_FILE):
                            ic._fail("Uninstall refuses ambiguous reservation evidence beside ownership.")
                        owner_path = path / ic.RUNTIME_SLOT_OWNERSHIP_FILE
                        owner = ic.read_json(owner_path)
                        snapshot_id = ic._string_property(ic._property(owner, "snapshot"), "id")
                        if snapshot_id not in snapshots:
                            ic._fail("Owned runtime has no validated snapshot.")
                        ic._validated_runtime_slot_ownership(validated, snapshots[snapshot_id], path.name)
                        immutable = path / ic.RUNTIME_SLOT_COMPLETION_FILE
                        if os.path.lexists(immutable):
                            snap = ic.read_json(root / "snapshots" / snapshot_id / ic.SNAPSHOT_PROVENANCE_FILE)
                            completion(path.name, snapshot_id, snap["payload"]["root"], snap["payload"]["version"])
                            guards[immutable] = ic._path_evidence(immutable)
                        guards[owner_path] = ic._path_evidence(owner_path)
                        plans.append((path, _tree(path, runtime=True), [immutable, owner_path]))
                for selected_version in marker_values:
                    if selected_version is not None:
                        selected_slot = root / "versions" / selected_version
                        owner = ic.read_json(selected_slot / ic.RUNTIME_SLOT_OWNERSHIP_FILE)
                        selected_snapshot = ic._string_property(ic._property(owner, "snapshot"), "id")
                        snap = ic.read_json(root / "snapshots" / selected_snapshot / ic.SNAPSHOT_PROVENANCE_FILE)
                        completion(selected_version, selected_snapshot, snap["payload"]["root"], snap["payload"]["version"])
                if plans:
                    if not slot.exists():
                        ic._fail("Uninstall target runtime is missing from owned inventory.")
                    completion(a.runtime_version, a.snapshot_id, a.expected_payload_root, a.expected_payload_version)
                elif snapshots:
                    selected = snapshots.get(a.snapshot_id)
                    if selected is None:
                        ic._fail("Uninstall snapshot identity is absent.")
                    ic._validate_snapshot_provenance(
                        context=a.context, expected_marketplace_id=a.expected_marketplace_id,
                        expected_plugin_id="agent-machines", snapshot_id=a.snapshot_id,
                        expected_payload_root=a.expected_payload_root, expected_payload_version=a.expected_payload_version,
                        durable_home=durable, environment={}, require_current_receipts=False,
                    )
                for snapshot_id in sorted(snapshots):
                    path = root / "snapshots" / snapshot_id
                    plans.append((path, _tree(path), [path / ic.SNAPSHOT_PROVENANCE_FILE]))
                for name in ("deploy-manifest.json", "launchers", "run", "logs", "cache"):
                    path = root / name
                    if os.path.lexists(path):
                        plans.append((path, _tree(path), []))
                hardlinks: dict[tuple, list[tuple[dict, Path]]] = {}
                removed_paths: set[Path] = set()
                for _, tree, _ in plans:
                    for path, evidence in tree.items():
                        if stat.S_ISREG(evidence[1][0]):
                            hardlinks.setdefault(evidence[0], []).append((tree, path))
                # Durable state is intentionally neither traversed nor changed.
                ic._path_evidence(root / "state")
                if not vr._reliable_process_enumeration():
                    ic._fail("Cannot prove runtime slots are inactive on this platform.")

                def refuse_live():
                    if not vr._iter_all_pids():
                        ic._fail("Cannot enumerate active runtime processes.")
                    if vr._versions_with_live_process(root):
                        ic._fail("Uninstall refuses runtime slots with active processes.")

                refuse_live()
                for i, path in enumerate(marker_paths):
                    if marker_values[i] is not None:
                        revalidate()
                        refuse_live()
                        path.unlink()
                        removed.append(path.name)
                        inventory[root].discard(path.name)
                        marker_values[i] = None
                        marker_evidence[i] = None
                        result["changed"] = True
                for target, tree, last_files in plans:
                    refuse_live()
                    result["pendingTarget"] = target.relative_to(root).as_posix()
                    if target.is_dir():
                        ordered = sorted((p for p in tree if p != target and p not in last_files),
                                         key=lambda p: (-len(p.parts), str(p)))
                        ordered += [p for p in last_files if p in tree] + [target]
                    else:
                        ordered = [target]
                    for path in ordered:
                        revalidate()
                        expected = tree[path]
                        if ic._is_link_or_junction(path):
                            info = path.lstat()
                            actual = (ic._stat_identity(info), ic._stat_metadata(info), os.readlink(path))
                        else:
                            actual = ic._path_evidence(path)
                        if actual != expected:
                            raise RevalidationRequired("artifact-changed")
                        # Ancestor identities must still name this exact captured tree.
                        for parent in path.parents:
                            if parent not in tree:
                                break
                            if ic._is_link_or_junction(parent) or ic._stat_identity(parent.lstat()) != tree[parent][0]:
                                raise RevalidationRequired("artifact-parent-changed")
                        is_directory = stat.S_ISDIR(path.lstat().st_mode) and not ic._is_link_or_junction(path)
                        if is_directory:
                            path.rmdir()
                        else:
                            path.unlink()
                        result["changed"] = True
                        removed_paths.add(path)
                        guards.pop(path, None)
                        # uv may hardlink identical package files across owned
                        # slots. Our unlink changes their ctime, not their bytes.
                        # Advance only that attributable metadata change.
                        if not is_directory and stat.S_ISREG(expected[1][0]):
                            for alias_tree, alias in hardlinks.get(expected[0], ()):
                                if alias in removed_paths:
                                    continue
                                before = alias_tree[alias]
                                after = ic._path_evidence(alias)
                                if (after is None or after[0] != before[0] or after[2] != before[2]
                                        or after[1][:3] != before[1][:3]):
                                    raise RevalidationRequired("hardlinked-artifact-changed")
                                alias_tree[alias] = after
                                if alias in guards:
                                    guards[alias] = after
                        parent = path.parent
                        if parent in tree:
                            tree[parent] = ic._path_evidence(parent)
                    removed.append(target.relative_to(root).as_posix())
                    if target.parent in inventory:
                        inventory[target.parent].discard(target.name)
                    result.pop("pendingTarget", None)
                revalidate()
                result["status"] = "ready" if result["changed"] else "preserved"
                result["reason"] = "cell-uninstalled" if result["changed"] else "cell-state-preserved"
                return result
            except RevalidationRequired as error:
                return {**result, "status": "revalidation-required", "reason": str(error)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("cell-repair", "cell-uninstall", "cell-attribute-legacy", "cell-deactivate"))
    parser.add_argument("--maintenance-token")
    for name in ("context", "durable-home", "expected-marketplace-id", "expected-payload-root",
                 "expected-payload-version", "snapshot-id", "runtime-version"):
        parser.add_argument(f"--{name}")
    for name in ("expected-namespace-generation", "expected-install-generation", "expected-activation-generation",
                 "expected-tombstone-activation-generation"):
        parser.add_argument(f"--{name}", type=ic._parse_cli_generation)
    parser.add_argument("--expect-tombstone-absent", action="store_true")
    for marker in ("current", "last-known-good"):
        group = parser.add_mutually_exclusive_group(required=False)
        group.add_argument(f"--expected-{marker}-version")
        group.add_argument(f"--expect-{marker}-absent", action="store_true")
    args = parser.parse_args(argv)
    if args.action in {"cell-repair", "cell-uninstall"}:
        required = (
            "context",
            "durable_home",
            "expected_marketplace_id",
            "expected_payload_root",
            "expected_payload_version",
            "snapshot_id",
            "runtime_version",
            "expected_namespace_generation",
            "expected_install_generation",
        )
        missing = [name for name in required if getattr(args, name) in (None, "")]
        if missing:
            parser.error(
                "the following arguments are required for "
                f"{args.action}: " + ", ".join(f"--{name.replace('_', '-')}" for name in missing)
            )
        for marker in ("current", "last_known_good"):
            if not (
                getattr(args, f"expected_{marker}_version") or getattr(args, f"expect_{marker}_absent")
            ):
                parser.error(
                    f"{args.action} requires either --expected-{marker.replace('_', '-')}-version "
                    f"or --expect-{marker.replace('_', '-')}-absent"
                )
    elif args.action == "cell-deactivate":
        required = (
            "context",
            "durable_home",
            "expected_marketplace_id",
            "expected_namespace_generation",
            "expected_install_generation",
            "expected_activation_generation",
        )
        missing = [name for name in required if getattr(args, name) in (None, "")]
        if missing:
            parser.error(
                "the following arguments are required for "
                f"{args.action}: " + ", ".join(f"--{name.replace('_', '-')}" for name in missing)
            )
        if (
            (args.expected_tombstone_activation_generation is None)
            == (not args.expect_tombstone_absent)
        ):
            parser.error(
                f"{args.action} requires exactly one of "
                "--expected-tombstone-activation-generation or --expect-tombstone-absent"
            )
    else:
        for name in ("context", "durable_home", "expected_marketplace_id"):
            if getattr(args, name) in (None, ""):
                parser.error(
                    "the following arguments are required for "
                    f"{args.action}: --{name.replace('_', '-')}"
                )
    try:
        print(json.dumps(lifecycle(args), separators=(",", ":")))
        return 0
    except (ic.InstallationContextError, OSError) as error:
        print(f"agent-machines lifecycle: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
