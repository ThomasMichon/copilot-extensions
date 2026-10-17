"""Operational identity of an installed source-qualified runtime."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_SOURCE_VERSION = re.compile(
    r"(?P<package>\d+\.\d+\.\d+(?:[.-]dev\d+)?"
    r"(?:\+[A-Za-z0-9][A-Za-z0-9._-]*)?)[.+]"
    r"source\.(?:[0-9a-f]{40}|[0-9a-f]{64})\.[0-9a-f]{16}"
)


def effective_runtime_version(
    package_version: str, *, prefix: Path | None = None, module_path: Path | None = None,
) -> str:
    candidate = prefix or Path(sys.prefix)
    if candidate.parent.name != "versions" or "+" not in candidate.name:
        return package_version
    # Numeric hash components must retain their opaque leading zeroes.
    match = _SOURCE_VERSION.fullmatch(candidate.name)
    if not match:
        return package_version
    package = package_version.lower().replace("-", ".").replace("_", ".")
    selected = match["package"].lower().replace("-", ".").replace("_", ".")
    if package != selected:
        return package_version
    if not (module_path or Path(__file__)).resolve().is_relative_to(candidate.resolve()):
        return package_version
    return candidate.name
