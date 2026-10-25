"""Read configured machine files without engine calls or provider discovery."""

from __future__ import annotations

import os
import re
from pathlib import Path

from machine_transport import MachineEntry, normalize_machine_entries, parse_machines_yaml

from . import harness_state


def read_entries(root: Path, *, read, supplier=None) -> dict[str, MachineEntry]:
    """Preserve the Picker's additive files and supplier-only fallback."""
    def parse(document):
        if not isinstance(document, dict) or not isinstance(document.get("machines"), dict):
            raise ValueError("Machine registry requires an explicit machines mapping")
        return parse_machines_yaml(document, require_alias=False)

    entries = {}
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
    if os.environ.get("COPILOT_EXTENSIONS_CONTEXT"):
        # A cell's namespace must not be substituted with the legacy registry.
        raise ValueError("Cell-scoped source registries are not supported by this reader")
    home = home_dir or harness_state.home()
    documents: dict[Path, dict | None] = {}

    def read(path: Path) -> dict | None:
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

    def read_registry(path: Path) -> dict:
        return read(path) or {}

    selection = harness_state.project_anchor_selection(project, home, reader=read_registry)
    if not selection.anchor or not Path(selection.anchor).is_absolute() or not Path(selection.anchor).is_dir():
        raise ValueError("Selected source project has no existing absolute local anchor")
    def supplier() -> Path:
        config = read(home / f".{project}" / "config.yaml") or {}
        knowledge = config.get("knowledge_repo")
        if not isinstance(knowledge, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", knowledge):
            raise ValueError("Source project has no configured machine registry")
        overlay = harness_state.project_anchor_selection(knowledge, home, reader=read_registry)
        if not overlay.anchor or not Path(overlay.anchor).is_absolute() or not Path(overlay.anchor).is_dir():
            raise ValueError("Configured machine supplier is unavailable")
        root = Path(overlay.anchor)
        for path in (root / ".agent-worktrees" / "machines.yaml", root / "machines.yaml"):
            if read(path) is not None:
                return path
        raise FileNotFoundError("Configured machine supplier has no registry")

    return read_entries(Path(selection.anchor), read=read, supplier=supplier)
