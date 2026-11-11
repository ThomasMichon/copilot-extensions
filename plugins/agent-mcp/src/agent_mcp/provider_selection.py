"""Qualify cached readiness providers without changing retained-path lookup."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path

from .config import ConfigError

_PROJECT_SETTINGS = (
    (".claude", "settings.json"),
    (".claude", "settings.local.json"),
    (".github", "copilot", "settings.json"),
    (".github", "copilot", "settings.local.json"),
)


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate settings field: {key}")
        result[key] = value
    return result


def _enabled(path: Path) -> dict[str, bool] | None:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        raise ConfigError(f"Cannot read plugin selection: {path}: {error}") from error
    try:
        raw = re.sub(
            r'"(?:[^"\\]|\\.)*"|//[^\r\n]*',
            lambda match: match[0] if match[0].startswith('"') else "",
            raw,
        )
        document = json.loads(raw, object_pairs_hook=_unique_fields)
        if not isinstance(document, dict):
            raise ValueError("settings must be an object")
        enabled = document.get("enabledPlugins", {})
        if not isinstance(enabled, dict) or any(type(value) is not bool for value in enabled.values()):
            raise ValueError("enabledPlugins must map plugin sources to booleans")
    except ValueError as error:
        raise ConfigError(f"Invalid plugin selection: {path}: {error}") from error
    return enabled


def effective_enablement() -> dict[str, bool]:
    home = Path(os.environ.get("COPILOT_HOME", Path.home() / ".copilot"))
    enabled = {}
    for name in ("settings.json", "settings.local.json"):
        enabled.update(_enabled(home / name) or {})
    project = os.environ.get("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT")
    workspace = Path(project).expanduser() if project else Path.cwd()
    if not workspace.is_absolute() or not workspace.is_dir():
        raise ConfigError("Selected installation project root must be an existing absolute directory")
    for root in (workspace, *workspace.parents):
        project = [_enabled(root.joinpath(*parts)) for parts in _PROJECT_SETTINGS]
        if any(layer is not None for layer in project):
            for layer in project:
                enabled.update(layer or {})
            break
    return enabled


def provider_filter() -> Callable[[Path], bool]:
    from . import config

    if config._marketplace_roots():
        return lambda _path: True
    enabled = effective_enablement()
    roots = [root.absolute() for root in config._plugin_roots()]

    def selected(path: Path) -> bool:
        for root in roots:
            try:
                parts = path.absolute().relative_to(root).parts
            except ValueError:
                continue
            if len(parts) == 4 and parts[2] in {"agents", "mcp"}:
                return enabled.get(f"{parts[1]}@{parts[0]}") is not False
        return True

    return selected


def discover_candidates(
    *, include: Callable[[Path], bool] | None = None,
) -> list[tuple[str, Path]]:
    from . import config

    candidates = []
    seen = set()
    marketplace_roots = config._marketplace_roots()
    roots = marketplace_roots or config._plugin_roots()
    prefix = "*" if marketplace_roots else "*/*"
    for root in roots:
        if not root.is_dir():
            continue
        for sub in ("agents", "mcp"):
            for ext in (".yaml", ".yml", ".json"):
                for path in sorted(root.glob(f"{prefix}/{sub}/*{ext}")):
                    if include is not None and not include(path):
                        continue
                    resolved = path.resolve()
                    if resolved in seen:
                        continue
                    seen.add(resolved)
                    candidates.append((config.normalize_bridge_name(path.name), path))
    return candidates
