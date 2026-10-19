"""Operational build identity for an installed, source-qualified runtime."""
from __future__ import annotations

from pathlib import Path
import re
import sys

_SOURCE_LOCAL = re.compile(
    r"(?:[a-z0-9]+\.)*source\.(?:[0-9a-f]{40}|[0-9a-f]{64})\.[0-9a-f]{16}"
)


def is_source_build_version(value: str) -> bool:
    from packaging.version import InvalidVersion, Version

    try:
        local = Version(value).local
    except (InvalidVersion, TypeError):
        return False
    if local is None:
        return False
    # Version.local normalizes numeric components and can erase hash zeroes.
    raw_local = value.strip().split("+", 1)[1].lower().replace("-", ".").replace("_", ".")
    return _SOURCE_LOCAL.fullmatch(raw_local) is not None


def effective_runtime_version(
    package_version: str, *, prefix: Path | None = None, module_path: Path | None = None,
) -> str:
    candidate = prefix or Path(sys.prefix)
    if candidate.parent.name != "versions" or "source." not in candidate.name:
        return package_version
    if not is_source_build_version(candidate.name):
        return package_version
    from packaging.version import InvalidVersion, Version

    try:
        package, runtime = Version(package_version), Version(candidate.name)
    except InvalidVersion:
        return package_version
    expected_local = (package.local + "." if package.local else "") + "source."
    if runtime.public != package.public or not (runtime.local or "").startswith(expected_local):
        return package_version
    installed = candidate.resolve()
    if not (module_path or Path(__file__)).resolve().is_relative_to(installed):
        return package_version
    return candidate.name
