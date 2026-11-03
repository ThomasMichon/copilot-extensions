"""Operational identity of an installed source-qualified or repaired runtime."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PACKAGE_VERSION = r"\d+\.\d+\.\d+(?:[.-]dev\d+)?(?:\+[A-Za-z0-9][A-Za-z0-9._-]*)?"
_SOURCE_VERSION = re.compile(
    rf"(?P<package>{_PACKAGE_VERSION})[.+]"
    r"source\.(?:[0-9a-f]{40}|[0-9a-f]{64})\.[0-9a-f]{16}"
    r"(?:\.profile\.[0-9a-f]{16})?"
    r"(?:\.repair\.[0-9a-f]{32})?"
)
_REPAIR_VERSION = re.compile(rf"(?P<package>{_PACKAGE_VERSION})[.+]repair\.[0-9a-f]{{32}}")


def is_source_build_version(value: str) -> bool:
    """Whether a generation carries a complete source-qualified identity."""
    return isinstance(value, str) and _SOURCE_VERSION.fullmatch(value) is not None


def effective_runtime_version(
    package_version: str, *, prefix: Path | None = None, module_path: Path | None = None,
) -> str:
    from agent_procutil._daemon_instance_identity import runtime_slot_paths

    candidate = prefix or Path(sys.prefix)
    location = runtime_slot_paths(candidate)
    if location is None or "+" not in location[0]:
        return package_version
    generation = location[0]
    # Numeric hash components must retain their opaque leading zeroes.
    match = _SOURCE_VERSION.fullmatch(generation) or _REPAIR_VERSION.fullmatch(generation)
    if not match:
        return package_version
    package = package_version.lower().replace("-", ".").replace("_", ".")
    selected = match["package"].lower().replace("-", ".").replace("_", ".")
    if package != selected:
        return package_version
    if not (module_path or Path(__file__)).resolve().is_relative_to(candidate.resolve()):
        return package_version
    return generation
