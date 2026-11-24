"""Repository-owned cache allowlists, independent of protected storage custody."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cache_limits import WorkBudget
from .cache_manifests import iter_cache_manifest
from .credential_fields import canonical_field
from .invocation import CACHE_TARGET_FIELDS as FIELDS
from .invocation import CACHE_MAINTENANCE_FIELD, cache_maintenance_owner


@dataclass(frozen=True)
class CommandTargets:
    entries: list[tuple[str, str]]
    service_owned: bool


class CacheTargetError(ValueError):
    """A declared allowlist is unavailable; another source cannot replace it."""


def _text(value: object) -> bool:
    return (
        isinstance(value, str) and bool(value.strip())
        and not any(ord(character) < 32 for character in value)
    )


def _entry(value: object) -> bool:
    return isinstance(value, str) and _text(value) and bool(value.strip().lstrip("/"))


def validate_configuration(data: dict[str, Any], label: str) -> None:
    """Validate target references without reading manifests or credentials."""
    for key in FIELDS & data.keys():
        items = data[key]
        if not isinstance(items, list):
            raise CacheTargetError(f"{label}: {key} must be an array")
        for item in items:
            valid = _entry(item) if key == "cache_entries" else _text(item)
            if valid:
                continue
            if (
                key == "cache_entries" and isinstance(item, list) and len(item) == 2
                and _entry(item[0]) and _text(item[1])
            ):
                continue
            raise CacheTargetError(f"{label}: invalid {key} reference")


def preferences(data: dict[str, Any], base: Path) -> dict[str, Any]:
    """Anchor a declaring layer's manifests before composing ordinary policy."""
    validate_configuration(data, "cache target configuration")
    cache_maintenance_owner(data)
    values = {key: data[key] for key in (FIELDS | {CACHE_MAINTENANCE_FIELD}) & data.keys()}
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
        return {
            **{key: composed["values"][key] for key in FIELDS},
            CACHE_MAINTENANCE_FIELD: cache_maintenance_owner(composed["values"]),
        }
    return {
        "cache_entries": [], "cache_manifests": [], CACHE_MAINTENANCE_FIELD: "local",
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


def _declared(
    policy: dict[str, Any], budget: WorkBudget | None = None,
) -> Iterator[tuple[str, str]]:
    for item in policy["cache_entries"]:
        if budget is not None and not budget.consume():
            return
        entry, field = (item, "password") if isinstance(item, str) else item
        yield entry.strip(), canonical_field(field.strip())
    try:
        for path in policy["cache_manifests"]:
            if budget is not None and not budget.available():
                return
            yield from _manifest_entries(Path(path), budget)
    except (OSError, UnicodeError, ValueError) as exc:
        raise CacheTargetError("configured cache manifest could not be read") from exc


def _manifest_entries(
    path: Path, budget: WorkBudget | None = None,
) -> Iterator[tuple[str, str]]:
    entries = (
        iter_cache_manifest(path) if budget is None
        else iter_cache_manifest(path, budget=budget)
    )
    for entry, field in entries:
        if not _entry(entry) or not _text(field):
            raise CacheTargetError("invalid cache manifest reference")
        yield entry, canonical_field(field)


def configured_source(
    cwd: str | None = None,
    *, budget: WorkBudget | None = None,
) -> Callable[[str | None], Iterator[tuple[str, str]]] | None:
    """Snapshot declared policy for bounded service-owned unlock warming."""
    policy = deepcopy(configured(cwd))
    if not any(policy[key] for key in FIELDS):
        return None

    def source(_machine: str | None) -> Iterator[tuple[str, str]]:
        return _declared(policy, budget)

    return source


def command_plan(args: Any) -> CommandTargets | None:
    """Snapshot targets and their access policy through the same configuration owner."""
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
        entries = list(dict.fromkeys(
            (entry, canonical_field(field or "password")) for entry, field in pairs if entry
        ))
        return CommandTargets(entries, cache_maintenance_owner(policy) == "selected-service")
    except RepositoryContextRequired:
        raise
    except (OSError, RuntimeError, ValueError):
        print(
            "Error: cache targets unavailable; check selected configuration and manifests",
            file=sys.stderr,
        )
        return None


def command_entries(args: Any) -> list[tuple[str, str]] | None:
    """Collect every CLI target without normalizing away root-entry anchors."""
    plan = command_plan(args)
    return plan.entries if plan is not None else None


def status_command(args: Any) -> int:
    """Augment the native cache owner's status with declared-policy metadata."""
    plan = command_plan(args)
    if plan is None:
        return 1
    if plan.service_owned:
        from .cache_maintenance_client import command

        return command(args, plan.entries, "status")
    from .cache import get_cache
    from .cache_recovery import status_command as native_status

    targets = plan.entries
    if getattr(args, "json", False):
        status = get_cache().status()
        status["configured_target_count"] = len(targets)
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0
    result = native_status(args)
    if result == 0:
        print(f"configured targets: {len(targets)}")
    return result
