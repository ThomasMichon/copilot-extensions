"""Repository-owned cache allowlists, independent of protected storage custody."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

from .cache_manifests import iter_cache_manifest
from .credential_fields import canonical_field

FIELDS = {"cache_entries", "cache_manifests"}
CAPABILITY = "repository-cache-targets-v1"


class CacheTargetError(ValueError):
    """A declared allowlist is unavailable; another source cannot replace it."""


def _text(value: object) -> bool:
    return (
        isinstance(value, str) and bool(value.strip())
        and not any(ord(character) < 32 for character in value)
    )


def validate_configuration(data: dict[str, Any], label: str) -> None:
    """Validate target references without reading manifests or credentials."""
    for key in FIELDS & data.keys():
        items = data[key]
        if not isinstance(items, list):
            raise CacheTargetError(f"{label}: {key} must be an array")
        for item in items:
            if _text(item):
                continue
            if (
                key == "cache_entries" and isinstance(item, list) and len(item) == 2
                and all(_text(part) for part in item)
            ):
                continue
            raise CacheTargetError(f"{label}: invalid {key} reference")


def preferences(data: dict[str, Any], base: Path) -> dict[str, Any]:
    """Anchor a declaring layer's manifests before composing ordinary policy."""
    validate_configuration(data, "cache target configuration")
    values = {key: data[key] for key in FIELDS & data.keys()}
    if "cache_manifests" in values:
        paths = []
        for value in values["cache_manifests"]:
            path = Path(os.path.expandvars(os.path.expanduser(value)))
            paths.append(str((base / path).absolute()))
        values["cache_manifests"] = paths
    return values


def configured(cwd: str | None = None) -> dict[str, Any]:
    from . import config, configuration
    from .extensions import get_registry

    machine = config.load_global_config(strict=True)
    path, repository = config._discover_repo_config(cwd, strict=True)
    root = Path(config.configuration_root(cwd)).resolve()
    extension = get_registry().collect_config(config.configuration_source(cwd), strict=True)
    for label, data in (
        ("global", machine), ("repository", repository), ("extension", extension),
    ):
        config._validate_config_data(data, f"{label} vault configuration")
    directory = path.parent if path is not None else root
    composed = configuration.compose_preferences(
        cwd, repository, machine, extension, repository_directory=directory,
    )
    if composed is not None:
        return {key: composed["values"][key] for key in FIELDS}
    return {
        "cache_entries": [], "cache_manifests": [],
        **preferences(machine, config._global_config_path().parent),
        **preferences(extension, root),
        **preferences(repository, directory),
    }


def manifest_path(value: str, cwd: str | None = None) -> Path:
    """Resolve an explicit relative manifest against the selected repository."""
    from . import config

    path = Path(value)
    if path.is_absolute():
        return path
    owner, _data = config._discover_repo_config(cwd, strict=True)
    base = owner.parent if owner is not None else Path(config.configuration_root(cwd))
    return base / path


def _declared(policy: dict[str, Any]) -> Iterator[tuple[str, str]]:
    for item in policy["cache_entries"]:
        entry, field = (item, "password") if isinstance(item, str) else item
        yield entry.strip(), canonical_field(field.strip())
    try:
        for path in policy["cache_manifests"]:
            yield from _manifest_entries(Path(path))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CacheTargetError("configured cache manifest could not be read") from exc


def _manifest_entries(path: Path) -> Iterator[tuple[str, str]]:
    for entry, field in iter_cache_manifest(path):
        if not _text(entry) or not _text(field):
            raise CacheTargetError("invalid cache manifest reference")
        yield entry, canonical_field(field)


def configured_source(
    cwd: str | None = None,
) -> Callable[[str | None], Iterator[tuple[str, str]]] | None:
    """Snapshot declared policy for bounded service-owned unlock warming."""
    policy = deepcopy(configured(cwd))
    if not any(policy.values()):
        return None

    def source(_machine: str | None) -> Iterator[tuple[str, str]]:
        return _declared(policy)

    return source


def command_entries(args: Any) -> list[tuple[str, str]] | None:
    """Collect every CLI target without normalizing away root-entry anchors."""
    from configuration_context import RepositoryContextRequired

    from .extensions import get_registry

    try:
        policy = configured()
        pairs = []
        for item in getattr(args, "entry", None) or []:
            entry, separator, field = item.partition(":")
            pairs.append((entry.strip(), field.strip() if separator else "password"))
        manifest = getattr(args, "manifest", None)
        if manifest:
            pairs.extend(_manifest_entries(manifest_path(manifest)))
        pairs.extend(_declared(policy))
        pairs.extend(get_registry().collect_cache_entries(getattr(args, "machine", None)))
        return list(dict.fromkeys(
            (entry, canonical_field(field or "password")) for entry, field in pairs if entry
        ))
    except RepositoryContextRequired:
        raise
    except (OSError, RuntimeError, ValueError):
        print(
            "Error: cache targets unavailable; check selected configuration and manifests",
            file=sys.stderr,
        )
        return None
