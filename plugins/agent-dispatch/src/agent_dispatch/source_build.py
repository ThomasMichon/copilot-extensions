"""Operational identity of an installed source-qualified runtime."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_SOURCE_VERSION = re.compile(
    r"(?P<package>\d+\.\d+\.\d+(?:[.-]dev\d+)?"
    r"(?:\+[A-Za-z0-9][A-Za-z0-9._-]*)?)[.+]"
    r"source\.(?:[0-9a-f]{40}|[0-9a-f]{64})\.[0-9a-f]{16}"
    r"(?:\.profile\.[0-9a-f]{16})?(?:\.repair\.[0-9a-f]{32})?"
)
_REPAIR_VERSION = re.compile(
    r"(?P<package>[0-9][A-Za-z0-9._-]*"
    r"(?:\+[A-Za-z0-9][A-Za-z0-9._-]*)?)(?P<separator>[.+])repair\.[0-9a-f]{32}"
)


def effective_runtime_version(
    package_version: str, *, prefix: Path | None = None, module_path: Path | None = None,
) -> str:
    try:
        from agent_procutil._daemon_instance_identity import runtime_slot_paths
    except ImportError:
        # Venv-less payload readers (native action admission) have no runtime slot.
        return package_version

    candidate = prefix or Path(sys.prefix)
    paths = runtime_slot_paths(candidate)
    if paths is None or "+" not in paths[0]:
        return package_version
    generation = paths[0]
    match = _SOURCE_VERSION.fullmatch(generation)
    if match is None:
        match = _REPAIR_VERSION.fullmatch(generation)
        if match is None or ("+" in match["package"]) != (match["separator"] == "."):
            return package_version
    package = package_version.lower().replace("-", ".").replace("_", ".")
    selected = match["package"].lower().replace("-", ".").replace("_", ".")
    if package != selected:
        if match.re is not _REPAIR_VERSION:
            return package_version
        try:
            from packaging.version import InvalidVersion, Version
        except ImportError:
            return package_version
        try:
            if Version(package_version) != Version(match["package"]):
                return package_version
        except (InvalidVersion, TypeError):
            return package_version
    if not (module_path or Path(__file__)).resolve().is_relative_to(candidate.resolve()):
        return package_version
    return generation
