"""Fresh-installer ownership admission; never import the installed application."""
from __future__ import annotations

import json
import os
import re
import runpy
from pathlib import Path
from types import ModuleType
from typing import Any

import tomllib


def _ordinary(path: Path) -> None:
    if path.is_symlink() or path.resolve() != path.absolute():
        raise RuntimeError(f"Update ownership path is indirect: {path}")


def _publication_api(engine: Path) -> tuple[dict[str, Any], ModuleType]:
    api = runpy.run_path(str(engine / "scripts/command_binding.py"))
    primitive = api["_load_primitive"](engine)
    return api, primitive


def validate_manager_ownership(
    root: Path, payload: Path, *, expected: dict[Path, bytes | None] | None = None,
) -> dict[Path, bytes | None]:
    """Admit only the Manager files this bootstrap is about to publish."""
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
    ownership = (
        payload.parent / "plugins/agent-worktrees/src/agent_worktrees"
        / "manager_launcher_ownership.py"
    )
    owns = runpy.run_path(str(ownership))["owns_launcher"] if not manager_seen else None
    commands = {}
    for name in ("worktree-manager", "worktree-manager.cmd", "worktree-manager.ps1"):
        path = local_bin / name
        _ordinary(path)
        if path.exists() and not path.is_file():
            raise RuntimeError(f"Foreign Manager command preserved: {path}")
        body = path.read_bytes() if path.is_file() else None
        if body is not None and owns is not None and not owns(name, body):
            raise RuntimeError(f"Foreign Manager command preserved: {path}")
        commands[path] = body
    if expected is not None and commands != expected:
        raise RuntimeError("Manager commands changed during construction")
    return commands
