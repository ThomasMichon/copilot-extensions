"""Optional, selected Manager runtime removal; never part of installation.

The finite archive describes layouts, not disposal permission. The current
installer owner's sealed inventory supplies that permission. No target code
is imported. A private external journal retains physical ownership across
partial deletion, without weakening completion or reservation-release rules.
That journal is this narrow Manager owner-protocol extension: the canonical
callback contract requires external recovery evidence but supplies no store.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
from functools import lru_cache
from pathlib import Path, PurePosixPath

import worktree_manager

from . import self_install_runtime
from .native_path_retirement import PinnedPath

REVISION = 1
ARCHIVE = (
    ("manager-sealed-payload-v1", "payload"),
    ("manager-sealed-application-v1", "application"),
)
_DETECTORS = dict(ARCHIVE)
_ARCHIVED_CONTRACT_V1 = {
    "schema": "copilot-extensions.manager-uninstall-contract",
    "version": 1, "identity": "worktree-manager.uninstall", "generation": 1,
    "supported_callers": [1], "target_inventory": 1, "service_policy": "quiescent-only",
}
_JOURNAL_SCHEMA = "worktree-manager.selected-runtime-removal"
_JOURNAL_DIR = ".historical-runtime-removals"
_MAX_RECORD = 16 * 1024 * 1024
_MAX_SLOTS = 256
_GENERATION = re.compile(r"[0-9][A-Za-z0-9._+-]{0,239}")


class _Preserve(ValueError):
    pass


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _plain(path: Path) -> None:
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise _Preserve("indirect-owner-path")


def _scope(root: Path) -> None:
    if (not root.is_absolute() or ".." in root.parts
            or any(value in str(root) for value in ("\r", "\n", "\0"))):
        raise _Preserve("invalid-owner-root")
    _plain(root)
    if root.exists() and not root.is_dir():
        raise _Preserve("owner-root-not-directory")
    for directory in (root / "versions", root / _JOURNAL_DIR):
        _plain(directory)
        if directory.exists() and not directory.is_dir():
            raise _Preserve("owner-directory-not-directory")


def _ordinary_generation(name: str) -> bool:
    return bool(_GENERATION.fullmatch(name)) and not name.endswith(".")


@lru_cache(maxsize=4)
def _load_owner(scripts: Path):
    installer = self_install_runtime.load_runtime_installer(scripts)
    if Path(installer.runtime.__file__).resolve() != (scripts / "versioned_runtime.py").resolve():
        raise _Preserve("current-runtime-provider-mismatch")
    source = scripts / "runtime_uninstall.py"
    specification = importlib.util.spec_from_file_location(
        "_manager_historical_runtime_owner_" + hashlib.sha256(str(source).encode()).hexdigest(),
        source,
    )
    if specification is None or specification.loader is None:
        raise _Preserve("current-runtime-owner-unavailable")
    owner = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(owner)
    if owner.runtime is not installer.runtime:
        raise _Preserve("current-runtime-owner-mismatch")
    return owner


def _owner():
    scripts = self_install_runtime.runtime_scripts(
        Path(worktree_manager.__file__).resolve().parent,
        self_install_runtime.running_payload_dir,
    )
    return _load_owner(scripts.resolve())


def _publication(owner):
    return owner.runtime._runtime_component("versioned_runtime_publication")


def _read_record(path: Path, owner) -> dict:
    _plain(path)
    before = _publication(owner).state_path(path, private=True)
    if before is None or before.size > _MAX_RECORD:
        raise _Preserve("owner-record-unavailable-or-oversized")
    record = owner._json(path)
    if _publication(owner).state_path(path, private=True) != before:
        raise _Preserve("owner-record-changed")
    return record


def _journal_path(root: Path, name: str) -> Path:
    return root / _JOURNAL_DIR / (hashlib.sha256(name.encode()).hexdigest() + ".json")


def _evidence_shape(evidence: dict) -> None:
    if (not isinstance(evidence, dict)
            or set(evidence) != {"root", "versions", "slot", "entries"}
            or not isinstance(evidence["entries"], dict)
            or len(evidence["entries"]) > 100000):
        raise _Preserve("invalid-removal-evidence")
    for name in evidence["entries"]:
        if not isinstance(name, str):
            raise _Preserve("invalid-inventory-relative-path")
        relative = PurePosixPath(name)
        if ("\\" in name or ":" in name
                or relative.is_absolute() or ".." in relative.parts
                or not relative.parts or relative.as_posix() != name):
            raise _Preserve("invalid-inventory-relative-path")


def _journal(root: Path, name: str, owner) -> dict | None:
    path = _journal_path(root, name)
    if not os.path.lexists(path):
        return None
    record = _read_record(path, owner)
    if (set(record) != {"schema", "version", "root", "name", "detector", "sha256", "evidence"}
            or record["schema"] != _JOURNAL_SCHEMA
            or type(record["version"]) is not int or record["version"] != REVISION
            or record["root"] != str(root) or record["name"] != name
            or record["detector"] not in _DETECTORS):
        raise _Preserve("unsupported-removal-journal")
    _evidence_shape(record["evidence"])
    if _digest(record["evidence"]) != record["sha256"]:
        raise _Preserve("removal-journal-evidence-changed")
    return record


def _protected(root: Path, name: str, owner) -> None:
    runtime = owner.runtime
    if runtime.is_dev_generation(name) or name.lower() == "dev":
        raise _Preserve("development-generation")
    if not runtime._reliable_process_enumeration():
        raise _Preserve("live-generation-evidence-unavailable")
    if name.lower() in runtime._gc_protected_versions(root, None, True, runtime.CURRENT_LINK):
        raise _Preserve("selected-referenced-or-live-generation")


def _recognize(root: Path, name: str, owner) -> str:
    """Finite retained detector knowledge; this never establishes disposal rights."""
    slot = root / "versions" / name
    _plain(slot)
    record = _read_record(slot / owner.INVENTORY, owner)
    complete = _read_record(slot / owner.runtime.COMPLETE_MARKER, owner)
    project, contract, _payload = owner._manager_layout(slot)
    if (record.get("schema") != "worktree-manager.artifact-inventory"
            or type(record.get("version")) is not int or record["version"] != 1
            or record.get("generation") != name
            or not isinstance(record.get("entries"), dict)
            or project.get("name") != "copilot-extensions-worktree-manager"
            or _read_record(contract, owner) != _ARCHIVED_CONTRACT_V1
            or set(complete) - {"version", "completed_at", "pid", "payload_hash"}
            or complete.get("version") != name
            or not isinstance(complete.get("completed_at"), str)
            or type(complete.get("pid")) is not int or complete["pid"] < 0
            or ("payload_hash" in complete and not isinstance(complete["payload_hash"], str))):
        raise _Preserve("unsupported-archived-runtime-layout")
    return ARCHIVE[int((slot / "application.json").is_file())][0]


def _snapshot(root: Path, name: str, owner) -> tuple[str, dict]:
    slot = root / "versions" / name
    detector = _recognize(root, name, owner)
    # This is the current installer's positive package, contract, completion,
    # interpreter and construction-inventory check, not archived recognition.
    record = owner._validate_inventory(slot)
    entries = dict(record["entries"])
    for control in (owner.INVENTORY, owner.runtime.COMPLETE_MARKER):
        _read_record(slot / control, owner)
        entries[control] = owner._state(slot / control)
    for relative, expected in entries.items():
        path = slot / relative
        if expected["kind"] == "file" and Path(owner.native_retained_path(path)).lstat().st_nlink != 1:
            raise _Preserve("hardlinked-runtime-artifact")
    evidence = {
        "root": owner._state(root), "versions": owner._state(root / "versions"),
        "slot": owner._state(slot), "entries": entries,
    }
    _evidence_shape(evidence)
    return detector, evidence


def _remaining(root: Path, name: str, evidence: dict, owner) -> dict:
    _evidence_shape(evidence)
    for path, key in ((root, "root"), (root / "versions", "versions")):
        if owner._state(path) != evidence[key]:
            raise _Preserve("runtime-parent-identity-changed")
    slot = root / "versions" / name
    _plain(slot)
    if not os.path.lexists(slot):
        return {}
    if owner._state(slot) != evidence["slot"]:
        raise _Preserve("runtime-slot-identity-changed")
    current = owner._tree(slot)
    for control in (owner.INVENTORY, owner.runtime.COMPLETE_MARKER):
        if os.path.lexists(slot / control):
            _read_record(slot / control, owner)
            current[control] = owner._state(slot / control)
    for relative, state in current.items():
        if evidence["entries"].get(relative) != state:
            raise _Preserve("runtime-artifact-changed-or-unowned")
        if state["kind"] == "file" and Path(
            owner.native_retained_path(slot / relative),
        ).lstat().st_nlink != 1:
            raise _Preserve("hardlinked-runtime-artifact")
    return current


def _finding(root: Path, name: str, owner) -> dict:
    base = {"revision": REVISION, "name": name, "resource": str(root / "versions" / name)}
    detector = "manager-runtime-unknown"
    try:
        if os.name != "nt":
            raise _Preserve("native-runtime-disposal-unsupported")
        if not _ordinary_generation(name):
            raise _Preserve("unsupported-runtime-generation")
        _protected(root, name, owner)
        journal = _journal(root, name, owner)
        if journal is not None:
            _remaining(root, name, journal["evidence"], owner)
            return dict(
                base, detector=journal["detector"], sha256=journal["sha256"],
                status="candidate", reason="owned-partial-removal-resumable",
            )
        detector = _recognize(root, name, owner)
        detector, evidence = _snapshot(root, name, owner)
        return dict(
            base, detector=detector, sha256=_digest(evidence),
            status="candidate", reason="sealed-manager-runtime-owned",
        )
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError) as error:
        return dict(
            base, detector=detector, sha256=None, status="preserved",
            reason=str(error) if isinstance(error, _Preserve) else "owner-evidence-unavailable-or-changed",
        )


def inspect(root: Path, bin_dir: Path | None = None) -> list[dict]:
    """Assess one explicit installation root, without discovering or changing it."""
    root = Path(root)
    try:
        _scope(root)
        if not root.exists():
            return []
        owner = _owner()
        versions = root / "versions"
        names = set()
        if versions.exists():
            for number, path in enumerate(versions.iterdir()):
                if number >= _MAX_SLOTS:
                    raise _Preserve("runtime-enumeration-bound")
                names.add(path.name)
        journals = root / _JOURNAL_DIR
        if journals.exists():
            for number, path in enumerate(journals.iterdir()):
                if number >= _MAX_SLOTS:
                    raise _Preserve("runtime-journal-enumeration-bound")
                record = _read_record(path, owner)
                name = record.get("name")
                if not isinstance(name, str) or not _ordinary_generation(name):
                    raise _Preserve("unsupported-removal-journal")
                if path != _journal_path(root, name):
                    raise _Preserve("unexpected-removal-journal-path")
                names.add(name)
        if len(names) > _MAX_SLOTS:
            raise _Preserve("runtime-enumeration-bound")
        return [_finding(root, name, owner) for name in sorted(names)]
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError):
        return [{
            "detector": "manager-runtime-unknown", "revision": REVISION,
            "name": "", "resource": str(root), "sha256": None,
            "status": "preserved", "reason": "runtime-owner-scope-unavailable",
        }]


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _journal_stamp(path: Path) -> tuple:
    _plain(path)
    state = path.lstat()
    return tuple(getattr(state, name) for name in (
        "st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns",
    ))


def _write_journal(path: Path, record: dict, owner) -> None:
    content = json.dumps(record, sort_keys=True, allow_nan=False).encode("utf-8")
    if len(content) > _MAX_RECORD:
        raise _Preserve("removal-journal-exceeds-bound")
    _plain(path.parent)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    _plain(path.parent)
    _sync_directory(path.parent.parent)
    descriptor = _publication(owner)._created_file(path)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    _sync_directory(path.parent)
    if _read_record(path, owner) != record:
        raise _Preserve("removal-journal-publication-changed")


def _remove_entry(path: Path, expected: dict, owner, *, parents: dict | None = None) -> None:
    if os.name != "nt" or expected.get("kind") not in {"file", "directory"}:
        raise _Preserve("native-runtime-disposal-unsupported")
    phase = "pin"
    try:
        with PinnedPath(path, directory=expected["kind"] == "directory") as pinned:
            phase = "identity"
            physical = pinned.state()
            changed = {
                key: {"expected": expected[key], "opened": physical[key]}
                for key in ("kind", "device", "inode", "mode") if physical[key] != expected[key]
            }
            if changed:
                raise _Preserve(f"runtime-artifact-changed-before-delete: {changed}")
            phase = "ancestor"
            for parent in path.parents:
                known = (parents or {}).get(str(parent))
                if known is not None and owner._state(parent) != known:
                    raise _Preserve("runtime-ancestor-identity-changed")
            phase = "native-ownership"
            if not owner.runtime._leases.windows_owned_file(pinned.fd):
                raise _Preserve("pinned-runtime-object-not-owned")
            phase = "content"
            if expected["kind"] == "file":
                proof = _publication(owner).state_fd(pinned.fd)
                if proof.digest != expected["digest"]:
                    raise _Preserve("pinned-runtime-content-changed")
            phase = "handle-disposition"
            pinned.dispose()
            phase = "handle-close"
    except (OSError, ValueError) as error:
        raise RuntimeError(
            f"runtime-{phase} ({expected['kind']} {path.name}): "
            f"{type(error).__name__}: {error}"
        ) from error


def _remove_selected(root: Path, name: str, finding: dict, owner) -> None:
    """Installer-owned callback, invoked only under canonical slot custody."""
    _scope(root)
    _protected(root, name, owner)
    path = _journal_path(root, name)
    journal = _journal(root, name, owner)
    if journal is None:
        detector, evidence = _snapshot(root, name, owner)
        if detector != finding["detector"] or _digest(evidence) != finding["sha256"]:
            raise _Preserve("reported-runtime-evidence-changed")
        journal = {
            "schema": _JOURNAL_SCHEMA, "version": REVISION,
            "root": str(root), "name": name, "detector": detector,
            "sha256": finding["sha256"], "evidence": evidence,
        }
        _write_journal(path, journal, owner)
    elif (journal["sha256"], journal["detector"]) != (finding["sha256"], finding["detector"]):
        raise _Preserve("reported-runtime-evidence-changed")
    proof = _publication(owner).state_path(path, private=True)
    stamp = _journal_stamp(path)
    journal_state = owner._state(path)
    entries = _remaining(root, name, journal["evidence"], owner)
    slot = root / "versions" / name
    evidence = journal["evidence"]
    parents = {
        str(root): evidence["root"], str(root / "versions"): evidence["versions"],
        str(slot): evidence["slot"], str(path.parent): owner._state(path.parent),
        **{str(slot / relative): state for relative, state in evidence["entries"].items()
           if state["kind"] == "directory"},
    }
    controls = (owner.INVENTORY, owner.runtime.COMPLETE_MARKER)
    order = sorted((value for value in entries if value not in controls),
                   key=lambda value: (-value.count("/"), value))
    order.extend(value for value in controls if value in entries)
    for relative in order:
        if _journal_stamp(path) != stamp:
            raise _Preserve("removal-journal-changed")
        if relative in controls:
            _protected(root, name, owner)
            _remaining(root, name, journal["evidence"], owner)
        _remove_entry(slot / relative, entries[relative], owner, parents=parents)
    if os.path.lexists(slot):
        _remaining(root, name, journal["evidence"], owner)
        _remove_entry(slot, evidence["slot"], owner, parents=parents)
    if _publication(owner).state_path(path, private=True) != proof:
        raise _Preserve("removal-journal-changed")
    _remove_entry(path, journal_state, owner, parents=parents)


def apply(root: Path, bin_dir: Path | None, finding: dict) -> dict:
    """Apply exactly one assessed finding; retain unknown or interrupted effects."""
    root = Path(root)
    resource = finding.get("resource", "") if isinstance(finding, dict) else ""
    result = {"resource": resource, "status": "preserved", "reason": "invalid-reported-runtime-evidence"}
    try:
        _scope(root)
        if os.name != "nt":
            return dict(result, reason="native-runtime-disposal-unsupported")
        if (not isinstance(finding, dict) or finding.get("status") != "candidate"
                or type(finding.get("revision")) is not int or finding["revision"] != REVISION
                or finding.get("detector") not in _DETECTORS
                or not isinstance(finding.get("name"), str)
                or not _ordinary_generation(finding["name"])
                or not isinstance(finding.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", finding["sha256"])
                or resource != str(root / "versions" / finding["name"])):
            return result
        owner = _owner()
        name = finding["name"]
        current = _finding(root, name, owner)
        if any(current.get(key) != finding.get(key) for key in
               ("detector", "revision", "name", "resource", "sha256", "status")):
            return dict(result, reason="reported-runtime-evidence-changed-or-protected")
        owner.runtime.remove_owned_candidate(
            root, name, remove=lambda candidate: _remove_selected(root, name, finding, owner),
        )
        if os.path.lexists(root / "versions" / name) or os.path.lexists(_journal_path(root, name)):
            raise RuntimeError("selected runtime removal did not settle")
        return dict(result, status="confirmed", reason="selected-owned-runtime-removed")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError) as error:
        pending = False
        if isinstance(finding, dict) and isinstance(finding.get("name"), str):
            pending = os.path.lexists(_journal_path(root, finding["name"]))
        return dict(
            result, status="failed" if pending else "preserved",
            reason="owned-removal-incomplete" if pending else (
                str(error) if isinstance(error, _Preserve) else "runtime-removal-custody-unavailable"
            ),
            detail=f"{type(error).__name__}: {error}"[:2048],
        )
