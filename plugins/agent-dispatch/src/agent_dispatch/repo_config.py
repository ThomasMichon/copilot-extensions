"""Repo-owned agent-dispatch configuration path helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path

CANONICAL_REPO_CONFIG_DIR = Path(".copilot-extensions") / "agent-dispatch"
LEGACY_REPO_CONFIG_DIR = Path(".agent-dispatch")
MARKETPLACE_OVERLAYS_DIR = CANONICAL_REPO_CONFIG_DIR / "marketplaces"
INSTALLATION_CONTEXT_ENV = "COPILOT_EXTENSIONS_CONTEXT"


def _load_installation_context() -> dict[str, object] | None:
    raw = os.environ.get(INSTALLATION_CONTEXT_ENV, "").strip()
    if not raw:
        return None
    try:
        if raw.startswith("{"):
            value = json.loads(raw)
        else:
            value = json.loads(Path(raw).expanduser().read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def installation_marketplace_id() -> str | None:
    context = _load_installation_context()
    if context is None:
        return None
    marketplace_id = context.get("marketplaceId")
    if not isinstance(marketplace_id, str) or not marketplace_id.strip():
        return None
    return marketplace_id.strip()


def canonical_repo_surface_dir(repo_root: str | Path, relative: str) -> Path:
    return Path(repo_root).expanduser() / CANONICAL_REPO_CONFIG_DIR / relative


def legacy_repo_surface_dir(repo_root: str | Path, relative: str) -> Path:
    return Path(repo_root).expanduser() / LEGACY_REPO_CONFIG_DIR / relative


def selected_repo_surface_dir(repo_root: str | Path, relative: str) -> Path:
    root = Path(repo_root).expanduser()
    canonical = canonical_repo_surface_dir(root, relative)
    if canonical.exists():
        return canonical
    legacy = legacy_repo_surface_dir(root, relative)
    if legacy.exists():
        return legacy
    return canonical


def overlay_repo_surface_dir(repo_root: str | Path, relative: str) -> Path | None:
    marketplace_id = installation_marketplace_id()
    if not marketplace_id:
        return None
    candidate = (
        Path(repo_root).expanduser()
        / MARKETPLACE_OVERLAYS_DIR
        / marketplace_id
        / relative
    )
    return candidate if candidate.exists() else None


def layered_repo_surface_dirs(repo_root: str | Path, relative: str) -> list[Path]:
    root = Path(repo_root).expanduser()
    layers: list[Path] = []
    base = selected_repo_surface_dir(root, relative)
    if base.exists():
        layers.append(base)
    overlay = overlay_repo_surface_dir(root, relative)
    if overlay is not None:
        layers.append(overlay)
    return layers


def repo_root_from_surface_path(path: str | Path, surface_dir: str) -> Path | None:
    current = Path(path).expanduser()
    subject = current if current.name == surface_dir else current.parent
    for candidate in (subject, *subject.parents):
        if candidate.name != surface_dir:
            continue
        if candidate.parent.name == LEGACY_REPO_CONFIG_DIR.name:
            return candidate.parent.parent
        parent = candidate.parent
        if (
            parent.name == "agent-dispatch"
            and parent.parent.name == ".copilot-extensions"
        ):
            return parent.parent.parent
        if (
            parent.parent.name == "marketplaces"
            and parent.parent.parent.name == "agent-dispatch"
            and parent.parent.parent.parent.name == ".copilot-extensions"
        ):
            return parent.parent.parent.parent.parent
    return None
