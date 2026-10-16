"""Lineage admission for generated project commands, not an owner arbiter."""

from __future__ import annotations

import json
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from packaging.version import InvalidVersion, Version

from . import project_command_templates as templates


@dataclass(frozen=True)
class CommandAdmission:
    """Immutable bytes actually checked at the admission boundary."""

    historical: bool
    stubs: Mapping[str, bytes | None]

    def __post_init__(self) -> None:
        captured = dict(self.stubs)
        if not all(
            isinstance(name, str) and (data is None or isinstance(data, bytes))
            for name, data in captured.items()
        ):
            raise TypeError("command admission requires immutable bytes and string names")
        object.__setattr__(self, "stubs", MappingProxyType(captured))

    def __bool__(self) -> bool:
        return self.historical


def _capture_admission_bytes(project: str) -> dict[str, bytes | None]:
    from . import installer as inst

    captured = {}
    for path, _ in inst._project_binstub_specs(project):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise inst.BinstubContentError(f"refusing linked/non-file project command for {project}")
        captured[inst._stub_key(path.name)] = path.read_bytes() if path.exists() else None
    return captured


def admit_transfer(project: str) -> CommandAdmission:
    """Capture the specific bytes a caller's explicit transfer authorizes."""
    return CommandAdmission(False, _capture_admission_bytes(project))


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _path_equal(left: str, right: str) -> bool:
    return bool(left and right) and Path(left).resolve() == Path(right).resolve()


def _forward(old: dict, current: dict) -> bool:
    try:
        return Version(old["plugin_version"]) <= Version(current["plugin_version"])
    except (KeyError, InvalidVersion, TypeError):
        return False


def snapshot_lineage(payload: Path, owner: dict, runtime: Path) -> bool:
    """A legacy snapshot must belong to this runtime's exact recorded source."""
    from . import installer as inst

    if owner.get("marketplace_id") or owner.get("install_receipt"):
        return False  # legacy-to-cell adoption belongs to the explicit transfer flow
    if payload.is_symlink() or payload.parent.resolve() != (runtime / "snapshots").resolve():
        return False
    source = _json(runtime / "deploy-manifest.json").get("source", {})
    manifest = _json(payload / "plugin.json")
    if not isinstance(source, dict):
        return False
    old = {"plugin_version": manifest.get("version")}
    return (
        _path_equal(str(source.get("path") or ""), owner.get("payload_root", ""))
        and source.get("plugin") == owner.get("plugin") == manifest.get("name")
        and bool(owner.get("repository"))
        and manifest.get("repository") == owner["repository"]
        and payload.name == manifest.get("version")
        and _forward(old, owner)
        and not _path_equal(str(payload), str(inst._payload_root()))
    )


def same_lineage(receipt: dict, owner: dict) -> bool:
    """Preserve physical equality; admit only proven forward relocations."""
    from . import installer as inst

    previous = receipt.get("owner", {})
    if inst._same_identity(previous, owner, inst._OWNER_IDENTITY_FIELDS):
        return True
    runtime = receipt.get("runtime", {})
    if not isinstance(runtime, dict) or not _path_equal(
        str(runtime.get("root") or ""), str(inst.install_dir())
    ):
        return False
    if not inst._same_identity(previous, owner, ("marketplace_id", "install_receipt", "plugin", "repository")):
        return False
    if not owner.get("repository") or not _forward(previous, owner):
        return False
    if owner.get("marketplace_id") and owner.get("install_receipt"):
        return previous.get("marketplace") == owner.get("marketplace")
    old = Path(previous.get("payload_root", ""))
    if previous.get("marketplace") != "local":
        return False
    if (
        _json(old / "plugin.json").get("version") == previous.get("plugin_version")
        and snapshot_lineage(old, owner, inst.install_dir())
    ):
        return True
    # A recorded legacy runtime may have moved from checkout to marketplace.
    # Its deploy manifest, not a matching repository URL alone, selects the owner.
    source = _json(inst.install_dir() / "deploy-manifest.json").get("source", {})
    return (
        isinstance(source, dict)
        and not owner.get("marketplace_id") and not owner.get("install_receipt")
        and owner.get("marketplace") not in ("", "local")
        and source.get("kind") == "marketplace"
        and source.get("plugin") == previous.get("plugin") == owner.get("plugin")
        and _path_equal(str(source.get("path") or ""), owner.get("payload_root", ""))
        and source.get("version") == owner.get("plugin_version")
    )


def _candidate_payload(project: str, contents: dict[str, bytes], owner: dict) -> Path | None:
    """Use only this owner or its recorded legacy runtime snapshots."""
    from . import installer as inst

    current = Path(owner["payload_root"])
    candidates = [current]
    snapshots = inst.install_dir() / "snapshots"
    if snapshots.is_dir():
        candidates += [p for p in snapshots.iterdir() if p.is_dir() and snapshot_lineage(p, owner, inst.install_dir())]
    for payload in candidates:
        # User-side shortcuts contain no owner identity; only a receipt can
        # attribute them. Receipt-free admission needs a payload-bearing family.
        for family in ("payload", "traced", "ambient"):
            expected = {
                inst._stub_key(name): text
                for name, text in templates.render(
                    project, payload, windows=inst.platform.system() == "Windows", family=family,
                    user_bin=inst.local_bin() if inst.registry_paths.installation_context() is None else None,
                ).items()
            }
            if contents and all(
                templates.matches(data, expected.get(name, "")) for name, data in contents.items()
            ):
                return payload
    return None


def validate(project: str, receipt: dict | None, owner: dict, identity: dict | None) -> CommandAdmission:
    """Validate one captured command and return its immutable admitted bytes."""
    from . import installer as inst

    paths = [p for p, _ in inst._project_binstub_specs(project)]
    captured = _capture_admission_bytes(project)
    contents = {name: data for name, data in captured.items() if data is not None}
    existing = [p for p in paths if captured[inst._stub_key(p.name)] is not None]
    if receipt is None:
        if not existing:
            return CommandAdmission(False, captured)
        if owner.get("marketplace_id") or owner.get("install_receipt"):
            raise inst.BinstubOwnershipError(f"refusing unreceipted command without cell ownership for {project}")
        payload = _candidate_payload(project, contents, owner)
        if payload is None:
            raise inst.BinstubContentError(f"refusing unknown unreceipted project command for {project}; verify ownership before --transfer")
        if identity is not None and not identity.get("path"):
            raise inst.BinstubOwnershipError(f"registered project identity is unavailable for {project}")
        return CommandAdmission(True, captured)
    if not same_lineage(receipt, owner):
        raise inst.BinstubOwnershipError(f"refusing project command ownership transfer for {project}; source/cell lineage is not proven")
    previous_identity = receipt["project"]
    relocating = not inst._same_identity(receipt["owner"], owner, inst._OWNER_IDENTITY_FIELDS)
    fields = ("name", "path", "remote") if relocating else ("name",)
    if relocating and identity is not None and not identity.get("path"):
        raise inst.BinstubOwnershipError(f"registered project identity is unavailable for {project}")
    if previous_identity.get("name") != inst._stub_key(project) or (
        identity is not None and not inst._same_identity(previous_identity, identity, fields)
    ):
        raise inst.BinstubOwnershipError(f"refusing project command identity change for {project}")
    allowed = {inst._stub_key(p.name) for p in paths}
    if {inst._stub_key(name) for name in receipt["stubs"]} != allowed or any(
        inst._stub_hash(receipt, p) != hashlib.sha256(contents[inst._stub_key(p.name)]).hexdigest()
        for p in existing
    ):
        raise inst.BinstubContentError(f"refusing modified project command for {project}; all sibling files are protected")
    if relocating:
        old = Path(receipt["owner"]["payload_root"])
        if not any(all(templates.matches(contents[inst._stub_key(p.name)], templates.render(
            project, old, windows=inst.platform.system() == "Windows", family=family,
            user_bin=inst.local_bin() if inst.registry_paths.installation_context() is None else None,
        )[p.name]) for p in existing) for family in ("current", "payload", "traced", "ambient")):
            raise inst.BinstubContentError(f"refusing unknown historical generated bytes for {project}")
    if not relocating and existing:
        desired = dict(inst._project_binstub_specs(project))
        if any(not templates.matches(contents[inst._stub_key(p.name)], desired[p]) for p in existing):
            if _candidate_payload(project, contents, owner) is None:
                raise inst.BinstubContentError(f"refusing unknown generated bytes for {project}")
            return CommandAdmission(True, captured)
    return CommandAdmission(relocating, captured)


def inventory() -> set[str]:
    """Include receipts even when edits removed a launcher's detectable header."""
    from . import installer as inst

    root = inst._command_arbitration_dir()
    if not root.is_dir():
        return set()
    return {p.stem for p in root.glob("*.json") if inst.cfg._PROJECT_NAME_RE.fullmatch(p.stem)}
