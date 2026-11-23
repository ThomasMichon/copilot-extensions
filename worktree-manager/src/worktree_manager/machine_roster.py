"""Read configured machine files without engine calls or provider discovery."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from machine_transport import MachineEntry, normalize_machine_entries, parse_machines_yaml

from . import harness_state


def read_entries(
    root: Path, *, read: Callable[[Path], dict[str, Any] | None],
    supplier: Callable[[], Path] | None = None,
) -> dict[str, MachineEntry]:
    """Preserve the Picker's additive files and supplier-only fallback."""
    def parse(document: object) -> dict[str, MachineEntry]:
        if not isinstance(document, dict) or not isinstance(document.get("machines"), dict):
            raise ValueError("Machine registry requires an explicit machines mapping")
        return parse_machines_yaml(document, require_alias=False)

    entries: dict[str, MachineEntry] = {}
    found = False
    for path in (root / "machines.yaml", root / ".agent-worktrees" / "machines.yaml"):
        document = read(path)
        if document is not None:
            found = True
            entries.update(parse(document))
    if not found:
        document = read(supplier()) if supplier is not None else None
        if document is None:
            raise FileNotFoundError("Configured machine registry is unavailable")
        entries = parse(document)
    return normalize_machine_entries(entries)


def selected_entries(project: str, *, home_dir: Path | None = None) -> dict[str, MachineEntry]:
    """Read one registered project's files, not every project's roster."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", project) or project in {".", ".."}:
        raise ValueError("Invalid source project")
    home = home_dir or harness_state.home()
    documents: dict[Path, dict[str, Any] | None] = {}

    def read(path: Path) -> dict[str, Any] | None:
        if path not in documents:
            try:
                path.lstat()
            except FileNotFoundError:
                if harness_state._has_dangling_link(path):
                    raise ValueError("Configured source contains a dangling link")
                documents[path] = None
            else:
                documents[path] = harness_state._read_yaml(path, strict=True)
        return documents[path]

    def read_registry(path: Path) -> dict[str, Any]:
        return read(path) or {}

    cell = bool(os.environ.get("COPILOT_EXTENSIONS_CONTEXT"))
    if cell:
        from .production_picker import project_config

        # Use only the accepted binding; a cold scalar fallback calls the engine.
        values = project_config._scalar_cache.values
        anchor = values.get((project, "repo-dir"))
        config_dir = values.get((project, "config-dir"))
        if not anchor or not config_dir or project_config.project_name() != project:
            raise ValueError("Selected installation has no prepared project binding")
        project_dir = Path(config_dir)
    else:
        selection = harness_state.project_anchor_selection(project, home, reader=read_registry)
        anchor = selection.anchor
        project_dir = home / f".{project}"
    if not anchor or not Path(anchor).is_absolute() or not Path(anchor).is_dir():
        raise ValueError("Selected source project has no existing absolute local anchor")
    def supplier() -> Path:
        config = read(project_dir / "config.yaml") or {}
        knowledge = config.get("knowledge_repo")
        if not isinstance(knowledge, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", knowledge):
            raise ValueError("Source project has no configured machine registry")
        inputs = None
        if cell:
            from .agent_plugin_runtime import _namespaced_plugin_root

            registry = _namespaced_plugin_root("agent-worktrees")
            if registry is None:
                raise ValueError("Selected installation has no scoped supplier registry")
            inputs = harness_state.ProfileInputRoots(registry, {})
        overlay = harness_state.project_anchor_selection(
            knowledge, home, reader=read_registry, require_project=False,
            input_roots=inputs,
        )
        if not overlay.anchor or not Path(overlay.anchor).is_absolute() or not Path(overlay.anchor).is_dir():
            raise ValueError("Configured machine supplier is unavailable")
        root = Path(overlay.anchor)
        for path in (root / ".agent-worktrees" / "machines.yaml", root / "machines.yaml"):
            if read(path) is not None:
                return path
        raise FileNotFoundError("Configured machine supplier has no registry")

    return read_entries(Path(anchor), read=read, supplier=supplier)
