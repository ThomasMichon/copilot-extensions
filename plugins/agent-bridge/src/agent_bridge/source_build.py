"""Operational build identity for an installed, source-qualified runtime."""
from __future__ import annotations

from pathlib import Path
import re
import sys

_SOURCE_LOCAL = re.compile(
    r"(?:[a-z0-9]+\.)*source\.(?:[0-9a-f]{40}|[0-9a-f]{64})\.[0-9a-f]{16}"
    r"(?:\.profile\.[0-9a-f]{16})?(?:\.repair\.[0-9a-f]{32})?"
)
_REPAIR_LOCAL = re.compile(r"(?:[a-z0-9]+\.)*repair\.[0-9a-f]{32}")


def _matches_local(value: str, pattern: re.Pattern) -> bool:
    from packaging.version import InvalidVersion, Version

    try:
        local = Version(value).local
    except (InvalidVersion, TypeError):
        return False
    if local is None:
        return False
    # Version.local normalizes numeric components and can erase hash zeroes.
    raw_local = value.strip().split("+", 1)[1].lower().replace("-", ".").replace("_", ".")
    return pattern.fullmatch(raw_local) is not None


def is_source_build_version(value: str) -> bool:
    return _matches_local(value, _SOURCE_LOCAL)


def is_repair_build_version(value: str) -> bool:
    return _matches_local(value, _REPAIR_LOCAL)


def effective_runtime_version(
    package_version: str, *, prefix: Path | None = None, module_path: Path | None = None,
) -> str:
    from agent_procutil._daemon_instance_identity import runtime_slot_paths

    candidate = prefix or Path(sys.prefix)
    paths = runtime_slot_paths(candidate)
    if paths is None:
        return package_version
    generation = paths[0]
    source = is_source_build_version(generation)
    if not source and not is_repair_build_version(generation):
        return package_version
    from packaging.version import InvalidVersion, Version

    try:
        package, runtime = Version(package_version), Version(generation)
    except InvalidVersion:
        return package_version
    expected_local = (package.local + "." if package.local else "") + ("source." if source else "repair.")
    if runtime.public != package.public or not (runtime.local or "").startswith(expected_local):
        return package_version
    installed = candidate.resolve()
    if not (module_path or Path(__file__)).resolve().is_relative_to(installed):
        return package_version
    return generation
