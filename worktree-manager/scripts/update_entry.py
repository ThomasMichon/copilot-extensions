"""Fresh-installer ownership admission; never import the installed application."""
from __future__ import annotations

import hashlib
import json
import os
import re
import runpy
from pathlib import Path
from types import ModuleType
from typing import Any

import tomllib

_ENGINE_PRE_REPAIR = {
    "agent-worktrees": "ceba4ec03556bc4bba11488f972e7bff9b7963fb31a856e58e88baae7c8916ad",
    "agent-worktrees.cmd": "a1eae8bd8c6bfe64559a91f48341e366f60c2c8256abf3600d65edc16e8773df",
    "agent-worktrees.ps1": "d1a014197a319d02148e20881124830054809bd808a79bd08eb5e196f4159c1e",
}


def _ordinary(path: Path) -> None:
    if path.is_symlink() or path.resolve() != path.absolute():
        raise RuntimeError(f"Update ownership path is indirect: {path}")


def _publication_api(engine: Path) -> tuple[dict[str, Any], ModuleType]:
    api = runpy.run_path(str(engine / "scripts/command_binding.py"))
    primitive = api["_load_primitive"](engine)
    return api, primitive


def _published_engine_sources(home: Path, engine: Path) -> tuple[Path, ...]:
    """Revalidate existing publication ownership, without executing old code."""
    root = home / ".agent-worktrees"
    manifest_path = root / "deploy-manifest.json"
    _ordinary(manifest_path)
    if not manifest_path.is_file():
        return ()
    before = manifest_path.read_bytes()
    api, primitive = _publication_api(engine)
    manifest = primitive.read_deployment_manifest(manifest_path)
    if (
        not isinstance(manifest, dict) or manifest.get("schema_version") != 3
        or manifest.get("service") != "agent-worktrees"
    ):
        raise RuntimeError("Prior Worktrees publication does not identify this engine")
    receipt = manifest.get("commandBinding")
    if not isinstance(receipt, dict):
        return ()
    candidates = [receipt.get("payloadRoot")]
    redirect = root / "payload-dir"
    _ordinary(redirect)
    redirect_before = redirect.read_bytes() if redirect.is_file() else None
    if redirect_before is not None:
        candidates.append(redirect_before.decode("utf-8").strip())
    sources: list[Path] = []
    refused: list[str] = []
    environment = dict(os.environ)
    # A historical-owner query is not a selection of the caller's current
    # payload. Keep account/policy inputs, but avoid that locator collision.
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    for value in candidates:
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise RuntimeError("Prior Worktrees publication has an invalid payload path")
        source = Path(value)
        _ordinary(source)
        if not source.is_dir() or source in sources:
            continue
        try:
            # This existing function only builds/validates metadata. The
            # write_legacy_manifest publisher is deliberately never called.
            api["publish_legacy_binding"](
                primitive, payload_root=source, plugin_id="agent-worktrees", legacy_root=root,
                environment=environment,
            )
        except (
            primitive.InstallationContextError, api["CommandOwnershipConflict"],
            api["SourceProvenanceUnavailable"], OSError, ValueError,
        ) as error:
            refused.append(str(error))
        else:
            sources.append(source)
    if manifest_path.read_bytes() != before or (
        (redirect.read_bytes() if redirect.is_file() else None) != redirect_before
    ):
        raise RuntimeError("Prior Worktrees publication changed during admission")
    if not sources and refused:
        raise RuntimeError("Prior Worktrees ownership refused: " + "; ".join(refused))
    return tuple(sources)


def _engine_helper_source(engine: Path, name: str) -> Path:
    return (
        engine / "scripts/installation-context/json-query.awk"
        if name == "agent-worktrees-update-json.awk" else engine / "bin" / name
    )


def validate_manager_ownership(root: Path, payload: Path) -> None:
    """Refuse foreign Manager registrations and command files before publication."""
    _ordinary(root)
    marker = root / "current-version"
    _ordinary(marker)
    if marker.exists():
        version = marker.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version) or version.endswith("."):
            raise RuntimeError("Manager selection is not an ordinary slot name")
        slot = root / "versions" / version
        _ordinary(slot)
        if slot.exists():
            payload_dir = slot / "payload" if (slot / "payload").is_dir() else slot
            metadata = payload_dir / "pyproject.toml"
            _ordinary(metadata)
            project = tomllib.loads(metadata.read_text(encoding="utf-8")).get("project", {})
            name = project.get("name", "")
            if not isinstance(name, str) or re.sub(r"[-_.]+", "-", name).lower() != (
                "copilot-extensions-worktree-manager"
            ):
                raise RuntimeError("Manager slot belongs to another package")
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    local_bin = home / ".local" / "bin"
    _ordinary(local_bin)
    engine = payload.parent / "plugins/agent-worktrees"
    previous_sources: tuple[Path, ...] | None = None
    for name in (
        *_ENGINE_PRE_REPAIR, "agent-worktrees-update-entry.sh", "agent-worktrees-update-entry.ps1",
        "agent-worktrees-update-json.awk",
    ):
        path = local_bin / name
        _ordinary(path)
        if not path.exists():
            continue
        if not path.is_file():
            raise RuntimeError(f"Foreign Worktrees command preserved: {path}")
        actual = path.read_bytes().replace(b"\r\n", b"\n")
        source = _engine_helper_source(engine, name)
        current = source.read_bytes().replace(b"\r\n", b"\n")
        if actual == current or hashlib.sha256(actual).hexdigest() == _ENGINE_PRE_REPAIR.get(name):
            continue
        if previous_sources is None:
            previous_sources = _published_engine_sources(home, engine)
        owned = False
        for previous in previous_sources:
            published = _engine_helper_source(previous, name)
            _ordinary(published)
            if published.is_file() and published.read_bytes().replace(b"\r\n", b"\n") == actual:
                owned = True
                break
        if not owned:
            raise RuntimeError(f"Foreign Worktrees command preserved: {path}")
    ownership = (
        payload.parent / "plugins/agent-worktrees/src/agent_worktrees"
        / "manager_launcher_ownership.py"
    )
    owns = runpy.run_path(str(ownership))["owns_launcher"]
    for name in ("worktree-manager", "worktree-manager.cmd", "worktree-manager.ps1"):
        path = local_bin / name
        _ordinary(path)
        if path.exists() and (not path.is_file() or not owns(name, path.read_bytes())):
            raise RuntimeError(f"Foreign Manager command preserved: {path}")
    registry = Path(os.environ.get(
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR",
        str(home / ".agent-worktrees/control-plane-providers.d"),
    ))
    _ordinary(registry)
    registrations = sorted(registry.glob("*.json"))
    selected = os.environ.get("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", "")
    if selected and selected != "worktree-manager":
        raise RuntimeError("Update bootstrap cannot replace a selected alternate provider")
    if len(registrations) > 1 and not selected:
        raise RuntimeError("Update bootstrap requires an unambiguous Manager provider")
    manager_seen = False
    for path in registrations:
        _ordinary(path)
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("provider") != "worktree-manager":
            if not selected:
                raise RuntimeError("Update bootstrap cannot replace another control-plane provider")
            continue
        if manager_seen:
            raise RuntimeError("Duplicate Manager registration")
        manager_seen = True
        primary = "worktree-manager.cmd" if os.name == "nt" else "worktree-manager"
        if (
            isinstance(document.get("schema_version"), bool)
            or document.get("schema_version") != 1
            or document.get("provider_root") != str(root.absolute())
            or document.get("command") != [str(local_bin / primary)]
        ):
            raise RuntimeError("Manager registration does not own the selected installation")
    if selected and not manager_seen:
        raise RuntimeError("Selected Manager provider is not registered")
