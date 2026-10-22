"""Plugin manifest dependencies and native marketplace publication metadata."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .marketplace import marketplace_manifest_path
from .settings import split_source


def is_harness_only(source: str) -> bool:
    name, _marketplace = split_source(source)
    return name.endswith("-harness") or "-harness-" in name


def report_diagnostic(item: dict) -> None:
    logger = logging.getLogger("plugin-resolve")
    if item["status"] == "native-origin-preserved":
        logger.warning(
            "%s prepared from target-native remote origin; original %s declaration "
            "preserved in repository settings",
            item["source"], item["marketplace"],
        )
    else:
        logger.warning(
            "%s declares companion %s (%s); caller must supply its execution owner; "
            "the dependency declaration does not authorize venue propagation",
            item["source"], item["dependency"], item["status"],
        )


def plugin_manifest(payload: Path) -> dict:
    for path in (payload / "plugin.json", payload / ".claude-plugin" / "plugin.json"):
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Plugin manifest must be an object")
            return value
    raise ValueError(f"Prepared plugin manifest is missing: {payload.name}")


def dependencies(manifest: dict, marketplace: str) -> list[str]:
    """Native dependency objects; retain qualified-string compatibility."""
    items = manifest.get("dependencies", [])
    if not isinstance(items, list):
        raise ValueError("Plugin dependencies must be a list")
    result = []
    for item in items:
        if isinstance(item, str) and item.strip():
            result.append(item if "@" in item else f"{item}@{marketplace}")
        elif isinstance(item, dict):
            name = item.get("name")
            registry = item.get("marketplace", marketplace)
            if not isinstance(name, str) or not name.strip() or not isinstance(registry, str) or not registry.strip():
                raise ValueError("Plugin dependency requires name and marketplace")
            result.append(f"{name}@{registry}")
        else:
            raise ValueError("Invalid plugin dependency")
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*", item)
           for item in result):
        raise ValueError("Plugin dependency requires a qualified plugin identity")
    return result


def publication_metadata(root: Path, source: str) -> tuple[dict, dict]:
    path = marketplace_manifest_path(root)
    if path is None:
        raise ValueError("Marketplace manifest is missing")
    catalog = json.loads(path.read_text(encoding="utf-8"))
    return _publication_metadata(catalog, source)


def _publication_metadata(catalog: dict, source: str) -> tuple[dict, dict]:
    name, _marketplace = split_source(source)
    entry = next((item for item in catalog.get("plugins", []) if item.get("name") == name), {})
    metadata = {key: value for key, value in catalog.items() if key != "plugins"}
    if isinstance(metadata.get("metadata"), dict):
        metadata["metadata"] = {key: value for key, value in metadata["metadata"].items()
                                if key != "pluginRoot"}
    return metadata, dict(entry)


def catalog_metadata(source: str, manifest: dict, metadata: dict, entry: dict) -> tuple[dict, dict]:
    """Produce a native-valid catalog, not merely shared-resolver-valid JSON."""
    name, marketplace = split_source(source)
    catalog = dict(metadata)
    owner = catalog.get("owner") or manifest.get("author")
    if isinstance(owner, str):
        owner = {"name": owner}
    if not isinstance(owner, dict) or not isinstance(owner.get("name"), str) or not owner["name"].strip():
        raise ValueError(f"Marketplace owner metadata is missing: {marketplace}")
    version = manifest.get("version") or entry.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"Plugin version metadata is missing: {source}")
    catalog.update(name=marketplace, owner=owner)
    plugin = {**entry, "name": name, "version": version}
    for key in ("description", "author", "homepage", "repository", "license", "keywords"):
        if key not in plugin and key in manifest:
            plugin[key] = manifest[key]
    if isinstance(plugin.get("author"), str):
        plugin["author"] = {"name": plugin["author"]}
    return catalog, plugin
