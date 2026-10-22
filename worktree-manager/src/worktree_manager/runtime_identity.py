"""Identity of the running interpreter, independent of mutable selectors."""

import sys
from pathlib import Path


def running_generation() -> str | None:
    prefix = Path(sys.prefix).resolve()
    if prefix.name == ".venv":
        prefix = prefix.parent
    return prefix.name if prefix.parent.name == "versions" else None


def route_version() -> str:
    from . import __version__

    return running_generation() or __version__
