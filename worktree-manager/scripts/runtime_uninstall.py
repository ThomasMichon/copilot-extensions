"""Non-provisioning, quiescent Manager removal through the installer owner."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

import versioned_runtime as runtime

SCRIPTS = Path(__file__).absolute().parent
INVENTORY = ".manager-artifacts.json"
CONTRACT = {
    "schema": "copilot-extensions.manager-uninstall-contract",
    "version": 1, "identity": "worktree-manager.uninstall", "generation": 1,
    "supported_callers": [1], "target_inventory": 1, "service_policy": "quiescent-only",
}


def _json(path: Path) -> dict:
    value = runtime._load_unique_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object: {path}")
    return value


def _contract(path: Path) -> None:
    _plain(path)
    if json.dumps(_json(path), sort_keys=True) != json.dumps(CONTRACT, sort_keys=True):
        raise ValueError(f"Unsupported Manager uninstall declaration: {path}")


def _indirect(path: Path) -> bool:
    value = path.lstat()
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & 0x400)


def _plain(path: Path) -> Path:
    path = path.absolute()
    for entry in reversed((path, *path.parents)):
        if os.path.lexists(entry) and _indirect(entry):
            raise ValueError(f"Indirect removal path refused: {entry}")
    return path


def _state(path: Path) -> dict:
    _plain(path.parent)
    before = path.lstat()
    if _indirect(path):
        # CPython's copied POSIX venv may still create this local library alias.
        if path.name != "lib64" or os.readlink(path) != "lib":
            raise ValueError(f"Unsupported linked artifact: {path}")
        kind, digest = "link", hashlib.sha256(os.readlink(path).encode()).hexdigest()
    elif stat.S_ISDIR(before.st_mode):
        kind, digest = "directory", ""
    elif stat.S_ISREG(before.st_mode):
        kind, digest = "file", hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        raise ValueError(f"Unsupported artifact type: {path}")
    after = path.lstat()
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    if any(getattr(before, key) != getattr(after, key) for key in fields):
        raise ValueError(f"Artifact changed during inspection: {path}")
    return {"kind": kind, "device": after.st_dev, "inode": after.st_ino,
            "mode": after.st_mode, "digest": digest}


def _tree(slot: Path) -> dict[str, dict]:
    result = {}

    def walk(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            if path.parent == slot and path.name in (INVENTORY, runtime.COMPLETE_MARKER):
                continue
            value = _state(path)
            result[path.relative_to(slot).as_posix()] = value
            if value["kind"] == "directory":
                walk(path)

    walk(slot)
    return result


def seal_inventory(slot: Path, *, frontdoors: dict[str, str], provider: dict) -> None:
    """Called only by installed Manager preparation, before slot completion."""
    _plain(slot)
    if os.path.lexists(slot / INVENTORY) or os.path.lexists(slot / runtime.COMPLETE_MARKER):
        raise ValueError("Manager inventory requires an unfinished, unsealed candidate")
    _contract(slot / "payload" / "references" / "uninstall-contract.json")
    record = {
        "schema": "worktree-manager.artifact-inventory", "version": 1,
        "generation": slot.name, "slot": _state(slot),
        "entries": _tree(slot),
        "frontdoors": {name: hashlib.sha256(body.encode("utf-8")).hexdigest()
                      for name, body in frontdoors.items()},
        "provider": provider,
    }
    descriptor = os.open(slot / INVENTORY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, sort_keys=True)
        stream.write("\n")


def _generated_bytecode(slot: Path, sealed: dict, current: dict) -> dict:
    """Attribute only added CPython caches to unchanged construction-owned sources."""
    import _imp

    extras = {name: state for name, state in current.items() if name not in sealed}
    directories = set()
    for name, state in extras.items():
        path = Path(name)
        if state["kind"] == "directory":
            continue
        match = re.fullmatch(r"(.+)\.cpython-[0-9]+(?:\.opt-[12])?\.pyc", path.name)
        if state["kind"] != "file" or path.parent.name != "__pycache__" or not match:
            raise ValueError(f"Unattributed Manager artifact: {slot / path}")
        source = path.parent.parent / (match[1] + ".py")
        source_name = source.as_posix()
        if sealed.get(source_name, {}).get("kind") != "file":
            raise ValueError(f"Manager bytecode has no sealed source: {slot / path}")
        content = (slot / path).read_bytes()
        if len(content) <= 16 or hashlib.sha256(content).hexdigest() != state["digest"]:
            raise ValueError(f"Manager bytecode changed or is malformed: {slot / path}")
        magic, flags = content[:4], int.from_bytes(content[4:8], "little")
        if magic[2:] != b"\r\n" or flags not in (0, 1, 3):
            raise ValueError(f"Unsupported Manager bytecode header: {slot / path}")
        source_path = slot / source
        if flags:
            valid = content[8:16] == _imp.source_hash(
                int.from_bytes(magic, "little"), source_path.read_bytes(),
            )
        else:
            info = source_path.stat()
            valid = (
                int.from_bytes(content[8:12], "little") == int(info.st_mtime) & 0xFFFFFFFF
                and int.from_bytes(content[12:16], "little") == info.st_size & 0xFFFFFFFF
            )
        if not valid:
            raise ValueError(f"Manager bytecode source binding changed: {slot / path}")
        directories.add(path.parent.as_posix())
    for name, state in extras.items():
        if state["kind"] == "directory" and name not in directories:
            raise ValueError(f"Unattributed Manager directory: {slot / name}")
    return extras


def _validate_inventory(slot: Path) -> dict:
    import tomllib

    _plain(slot)
    inventory = slot / INVENTORY
    publication = runtime._runtime_component("versioned_runtime_publication")
    if publication.state_path(inventory, private=True) is None:
        raise ValueError(f"Unsupported Manager target: missing construction inventory in {slot}")
    record = _json(inventory)
    current = _tree(slot)
    if (
        set(record) != {"schema", "version", "generation", "slot", "entries", "frontdoors", "provider"}
        or record["schema"] != "worktree-manager.artifact-inventory"
        or type(record["version"]) is not int or record["version"] != 1
        or record["generation"] != slot.name or record["slot"] != _state(slot)
        or not isinstance(record["entries"], dict)
        or any(name not in current or current[name] != state for name, state in record["entries"].items())
    ):
        raise ValueError(f"Manager construction inventory changed or is unsupported: {slot}")
    extras = _generated_bytecode(slot, record["entries"], current)
    metadata = slot / "payload" / "pyproject.toml"
    project = tomllib.loads(metadata.read_text(encoding="utf-8"))["project"]
    if (
        project["name"] != "copilot-extensions-worktree-manager"
        or runtime.package_version(slot.name) != project["version"]
    ):
        raise ValueError("Removal target belongs to another package")
    _contract(slot / "payload" / "references" / "uninstall-contract.json")
    if not runtime.is_complete(slot.parent.parent, slot.name):
        raise ValueError("Removal target is not a completed canonical Manager runtime")
    if runtime.slot_python(slot.parent.parent, slot.name) is None:
        raise ValueError("Removal target has no canonical interpreter")
    source = runtime._runtime_component("versioned_runtime_source")
    source.read_source_identity(slot / "payload", project["version"], plugin="worktree-manager")
    if not isinstance(record["frontdoors"], dict) or not isinstance(record["provider"], dict):
        raise ValueError("Malformed Manager integration ownership")
    return dict(record, entries={**record["entries"], **extras})


def _integration(root: Path, record: dict) -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    local_bin = _plain(home / ".local" / "bin")
    registry = _plain(Path(os.environ.get("AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR")
                           or home / ".agent-worktrees" / "control-plane-providers.d").expanduser())
    provider = registry / "worktree-manager.json"
    expected = record["provider"]
    primary = "worktree-manager.cmd" if os.name == "nt" else "worktree-manager"
    if (
        expected.get("provider") != "worktree-manager"
        or type(expected.get("schema_version")) is not int or expected["schema_version"] != 1
        or expected.get("provider_root") != str(root)
        or expected.get("command") != [str(local_bin / primary)]
    ):
        raise ValueError("Inventory names another Manager registration")
    selected = os.environ.get("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", "").strip()
    if selected and selected != "worktree-manager":
        raise ValueError("Another control-plane provider is selected")
    publication = runtime._runtime_component("versioned_runtime_publication")
    owned = []
    for name, digest in record["frontdoors"].items():
        if name not in ("worktree-manager", "worktree-manager.cmd", "worktree-manager.ps1"):
            raise ValueError("Inventory names an unknown frontdoor")
        path = local_bin / name
        state = publication.state_path(path)
        if state is not None:
            if state.digest != digest:
                raise ValueError(f"Changed or foreign Manager frontdoor preserved: {path}")
            owned.append(path)
    for name in ("worktree-manager", "worktree-manager.cmd", "worktree-manager.ps1"):
        if os.path.lexists(local_bin / name) and name not in record["frontdoors"]:
            raise ValueError("Unattributed Manager frontdoor preserved")
    if publication.state_path(provider) is None:
        if owned:
            raise ValueError("Missing Manager registration cannot attribute shared frontdoors")
    else:
        if _json(provider) != expected:
            raise ValueError("Changed or foreign Manager registration preserved")
        owned.append(provider)
    return owned


@contextmanager
def _daemon_custody(root: Path, *, create: bool):
    path = _plain(root / "mux-daemon.spawn.lock")
    publication = runtime._runtime_component("versioned_runtime_publication")
    before = publication.state_path(path)
    if before is None and not create:
        if os.path.lexists(root / "mux-daemon-routing"):
            raise ValueError("Manager routing history has no attributable daemon spawn lease")
        yield
        return
    if before is not None and os.name == "nt" and before.size == 0 and not create:
        raise ValueError("An empty Manager daemon lease needs its owner to settle first")
    source = _plain(SCRIPTS.parent / "src" / "worktree_manager" / "mux_file_lock.py")
    spec = importlib.util.spec_from_file_location("_manager_uninstall_lock", source)
    if spec is None or spec.loader is None:
        raise ValueError("Manager daemon lease owner is unavailable")
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    if before is None:
        flags |= os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(path, flags, 0o600), "r+b") as stream:
        opened, named = os.fstat(stream.fileno()), path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            or before is not None and (opened.st_dev, opened.st_ino) != (before.device, before.inode)
        ):
            raise ValueError("Manager daemon spawn lease changed")
        if not owner._try_lock_file_once(stream):
            raise ValueError("Manager daemon is in use; no process was stopped")
        try:
            yield
        finally:
            owner._unlock_file(stream)


def _quiescent(root: Path) -> None:
    if not runtime._reliable_process_enumeration():
        raise ValueError("Reliable process enumeration is unavailable; removal cannot prove quiescence")
    for name in ("dev-claim.json", "mux-daemon.lock"):
        if os.path.lexists(root / name):
            raise ValueError(f"Manager lifecycle evidence requires its owner to settle first: {name}")
    if runtime._versions_with_live_process(root) or runtime._running_pids(root):
        raise ValueError("Manager runtime is in use; no process was stopped")


def _plan(root: Path, *, daemon_held: bool = False) -> dict:
    _plain(root)
    if not root.exists():
        return {"versions": [], "selectors": {}, "integrations": [], "inventories": {}}
    publication = runtime._runtime_component("versioned_runtime_publication")
    selectors = {root / name: publication.state_path(root / name)
                 for name in ("current-version", "last-known-good")}
    versions_dir = _plain(root / "versions")
    versions = sorted(path.name for path in versions_dir.iterdir()) if versions_dir.exists() else []
    if not versions:
        if any(value is not None for value in selectors.values()):
            raise ValueError("Unsupported target: selectors without owned runtime slots")
        return {"versions": [], "selectors": selectors, "integrations": [], "inventories": {}}
    records = {}
    for version in versions:
        runtime._validate_dev_previous_version(version)
        if runtime.is_dev_generation(version):
            raise ValueError("Development runtime custody must be settled by its owner")
        records[version] = _validate_inventory(versions_dir / version)
    selected = {}
    for path, state in selectors.items():
        if state is None:
            raise ValueError("Supported removal requires both canonical selectors")
        version = publication.CopyPublication._bytes(path, state).decode("utf-8").strip()
        if version not in records:
            raise ValueError("Selector names an unowned Manager generation")
        selected[path.name] = version
    integrations = _integration(root, records[selected["current-version"]])
    if daemon_held:
        _quiescent(root)
    else:
        with _daemon_custody(root, create=False):
            _quiescent(root)
    return {"versions": versions, "selectors": selectors, "integrations": integrations,
            "inventories": records,
            "control_states": {
                path: publication.state_path(path, private=True)
                for version in versions for path in (
                    versions_dir / version / INVENTORY,
                    versions_dir / version / runtime.COMPLETE_MARKER,
                )
            },
            "integration_states": {path: publication.state_path(path) for path in integrations}}


def _remove(root: Path, plan: dict, removed: list[str]) -> None:
    publication = runtime._runtime_component("versioned_runtime_publication")
    leases = runtime._runtime_component("versioned_runtime_leases")
    with _daemon_custody(root, create=True):
        with leases.batch_removal_custody(root, plan["versions"]):
            with publication.target_leases(root, plan["integrations"]):
                _remove_locked(root, plan, removed, publication)


def _remove_locked(root: Path, plan: dict, removed: list[str], publication) -> None:
    if _plan(root, daemon_held=True) != plan:
        raise ValueError("Manager selection or artifacts changed before removal")
    for path in [*plan["integrations"], *plan["selectors"]]:
        expected = plan["selectors"].get(path, plan["integration_states"].get(path))
        if publication.state_path(path) != expected:
            raise ValueError(f"Manager publication changed before withdrawal: {path}")
        path.unlink()
        removed.append(str(path))
    for version in plan["versions"]:
        slot = root / "versions" / version
        record = plan["inventories"][version]
        for relative in sorted(record["entries"], key=lambda value: (-value.count("/"), value)):
            path = slot / relative
            if _state(path) != record["entries"][relative]:
                raise ValueError(f"Manager artifact changed during removal: {path}")
            if record["entries"][relative]["kind"] == "directory":
                path.rmdir()
            else:
                path.unlink()
            removed.append(str(path))
        for name in (runtime.COMPLETE_MARKER, INVENTORY):
            path = slot / name
            if publication.state_path(path, private=True) != plan["control_states"][path]:
                raise ValueError(f"Manager completion/inventory changed: {path}")
            path.unlink()
        slot.rmdir()
        removed.append(str(slot))
    if list((root / "versions").iterdir()):
        raise ValueError("Concurrent runtime construction remains; removal is incomplete")


def uninstall(root: Path, *, apply: bool, as_json: bool) -> int:
    removed: list[str] = []
    root = root.absolute()
    result = {"schema": "worktree-manager.uninstall-result", "version": 1,
              "contract": "worktree-manager.uninstall", "generation": 1,
              "root": str(root), "removed": removed}
    try:
        root = _plain(root).resolve()
        result["root"] = str(root)
        _contract(SCRIPTS.parent / "references" / "uninstall-contract.json")
        if apply and (
            Path(sys.executable).resolve().is_relative_to(root)
            or Path(sys.prefix).resolve().is_relative_to(root)
            or SCRIPTS.resolve().is_relative_to(root)
        ):
            raise ValueError(
                "Uninstall requires a base Python and installer payload outside the target. "
                f"Base interpreter: {getattr(sys, '_base_executable', sys.executable)}; "
                f"installer: {SCRIPTS / 'runtime_install.py'}. "
                "If that installer is inside the target, use an extracted supported release "
                "outside the target; run its scripts/runtime_install.py with "
                f"--root {str(root)!r} --uninstall --apply."
            )
        plan = _plan(root)
        if not plan["versions"]:
            result["status"] = "absent"
        elif not apply:
            result.update(status="planned", versions=plan["versions"],
                          frontdoors=[str(path) for path in plan["integrations"]])
        else:
            _remove(root, plan, removed)
            result["status"] = "removed"
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        result.update(status="incomplete" if removed else "refused", reason=str(error))
    if as_json:
        print(json.dumps(result))
    else:
        print(f"Manager uninstall: {result['status']}" + (
            f": {result['reason']}" if "reason" in result else ""
        ))
    return 1 if result["status"] in ("refused", "incomplete") else 0
