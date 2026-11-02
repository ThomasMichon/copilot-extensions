"""Identity of the running interpreter, independent of mutable selectors."""

import sys
from pathlib import Path


def running_slot() -> Path | None:
    prefix = Path(sys.prefix).resolve()
    if prefix.name == ".venv":
        prefix = prefix.parent
    return prefix if prefix.parent.name == "versions" else None


def running_generation() -> str | None:
    slot = running_slot()
    return slot.name if slot is not None else None


def running_application_slot() -> Path | None:
    slot = running_slot()
    return slot if slot is not None and (slot / "application.json").is_file() else None


def route_version() -> str:
    from . import __version__

    return running_generation() or __version__
