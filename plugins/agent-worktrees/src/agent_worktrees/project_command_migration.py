"""Lineage admission for generated project commands, not an owner arbiter."""

from __future__ import annotations

import json
from pathlib import Path

from packaging.version import InvalidVersion, Version

from . import project_command_templates as templates


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
    return (
        _json(old / "plugin.json").get("version") == previous.get("plugin_version")
        and snapshot_lineage(old, owner, inst.install_dir())
    )


def _candidate_payload(project: str, paths: list[Path], owner: dict) -> Path | None:
    """Use only this owner or its recorded legacy runtime snapshots."""
    from . import installer as inst

    current = Path(owner["payload_root"])
    candidates = [current]
    snapshots = inst.install_dir() / "snapshots"
    if snapshots.is_dir():
        candidates += [p for p in snapshots.iterdir() if p.is_dir() and snapshot_lineage(p, owner, inst.install_dir())]
    for payload in candidates:
        for family in ("current", "traced", "ambient"):
            expected = templates.render(project, payload, windows=inst.platform.system() == "Windows", family=family)
            if paths and all(
                not p.is_symlink() and templates.matches(p.read_bytes(), expected.get(p.name, ""))
                for p in paths
            ):
                return payload
    return None


def validate(project: str, receipt: dict | None, owner: dict, identity: dict | None) -> bool:
    """Validate the entire platform command; return whether it is historical."""
    from . import installer as inst

    paths = [p for p, _ in inst._project_binstub_specs(project)]
    existing = [p for p in paths if p.exists() or p.is_symlink()]
    if any(p.is_symlink() or not p.is_file() for p in existing):
        raise inst.BinstubContentError(f"refusing linked/non-file project command for {project}")
    if receipt is None:
        if not existing:
            return False
        if owner.get("marketplace_id") or owner.get("install_receipt"):
            raise inst.BinstubOwnershipError(f"refusing unreceipted command without cell ownership for {project}")
        payload = _candidate_payload(project, existing, owner)
        if payload is None:
            raise inst.BinstubContentError(f"refusing unknown unreceipted project command for {project}; verify ownership before --transfer")
        if identity is not None and not identity.get("path"):
            raise inst.BinstubOwnershipError(f"registered project identity is unavailable for {project}")
        return True
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
        inst._stub_hash(receipt, p) != inst._file_sha256(p) for p in existing
    ):
        raise inst.BinstubContentError(f"refusing modified project command for {project}; all sibling files are protected")
    if relocating:
        old = Path(receipt["owner"]["payload_root"])
        if not any(all(templates.matches(p.read_bytes(), templates.render(
            project, old, windows=inst.platform.system() == "Windows", family=family
        )[p.name]) for p in existing) for family in ("current", "traced", "ambient")):
            raise inst.BinstubContentError(f"refusing unknown historical generated bytes for {project}")
    if not relocating and existing:
        desired = dict(inst._project_binstub_specs(project))
        if any(not templates.matches(p.read_bytes(), desired[p]) for p in existing):
            if _candidate_payload(project, existing, owner) is None:
                raise inst.BinstubContentError(f"refusing unknown generated bytes for {project}")
            return True
    return relocating


def inventory() -> set[str]:
    """Include receipts even when edits removed a launcher's detectable header."""
    from . import installer as inst

    root = inst._command_arbitration_dir()
    if not root.is_dir():
        return set()
    return {p.stem for p in root.glob("*.json") if inst.cfg._PROJECT_NAME_RE.fullmatch(p.stem)}
