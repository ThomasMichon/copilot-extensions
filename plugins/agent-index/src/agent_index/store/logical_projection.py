"""Logical coverage carried by the existing source-domain commit receipt."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def physical_table(path: Path, name: str, version: int) -> dict[str, Any]:
    identity = (path / f"{name}.lance").stat()
    if not identity.st_ino:
        raise OSError("Projection storage lacks a stable local file identity")
    return {
        "version": version,
        "storage": [identity.st_dev, identity.st_ino, identity.st_ctime_ns],
    }


def physical_tables(path: Path, versions: dict[str, int]) -> dict[str, Any] | None:
    try:
        return {name: physical_table(path, name, version) for name, version in versions.items()}
    except OSError:
        log.warning("Logical projection identity is unavailable; retaining physical coverage", exc_info=True)
        return None


def current_projection(
    receipt: dict[str, Any], physical: dict[str, Any] | None, *, complete: bool = True,
) -> dict[str, Any] | None:
    """Unproven optional metadata never licenses logical coverage reuse."""
    value = receipt.get("logical_projection")
    if physical is None or value is None:
        return None
    if (not isinstance(value, dict) or type(value.get("version")) is not int
            or value["version"] != 1
            or not isinstance(value.get("epoch"), str) or not value["epoch"]
            or type(value.get("generation")) is not int or value["generation"] < 0
            or not isinstance(value.get("tables"), dict)
            or not isinstance(value.get("witness"), dict)
            or value["tables"].keys() != value["witness"].keys()
            or any(not isinstance(name, str) or type(generation) is not int
                   or not 0 <= generation <= value["generation"]
                   for name, generation in value["tables"].items())):
        log.warning("Invalid logical projection metadata; retaining physical coverage")
        return None
    witness = value["witness"]
    if complete and witness.keys() != physical.keys():
        return None
    if any(witness.get(name) != identity for name, identity in physical.items()):
        return None
    return value


def commit_projection(
    receipt: dict[str, Any],
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    physical_only: set[str],
    scoped: dict[str, set[str]] | None = None,
) -> set[str] | None:
    """Publish once at the outer clean commit, never at an intermediate effect."""
    previous = current_projection(receipt, before)
    if after is None:
        receipt.pop("logical_projection", None)
        return None
    generations = previous["tables"] if previous is not None else {}
    changed = {
        name for name, identity in after.items()
        if before is None or before.get(name) != identity
    } | ((before or {}).keys() - after.keys())
    # Creation, replacement and rollback are not physical-only maintenance.
    preserved = {
        name for name in physical_only & (before or {}).keys() & after.keys()
        if before[name]["storage"][:2] == after[name]["storage"][:2]
        and before[name]["version"] <= after[name]["version"]
    } if before is not None else set()
    logical = changed - preserved
    generation = (previous["generation"] if previous is not None else 0) + bool(logical)
    receipt["logical_projection"] = {
        "version": 1,
        "epoch": previous["epoch"] if previous is not None else uuid.uuid4().hex,
        "generation": generation,
        "tables": {
            name: generation if name in logical else generations.get(name, 0) for name in after
        },
        "witness": after,
    }
    from .bucket_projection import commit_buckets

    stable = {
        name: buckets for name, buckets in (scoped or {}).items()
        if before is not None and name in before and name in after
        and before[name]["storage"][:2] == after[name]["storage"][:2]
        and before[name]["version"] <= after[name]["version"]
    }
    commit_buckets(previous, receipt["logical_projection"], stable)
    return logical if before is not None else None
