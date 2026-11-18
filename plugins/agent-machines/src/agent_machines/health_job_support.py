"""Read installed finite-collection support without importing the selected package."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re
import sys
from typing import cast

from . import health_slots as slots


def require_collection_support(python: Path) -> None:
    """Keep Windows qualification and isolate Linux support to one installed venv."""
    if sys.platform == "win32":
        from .health_enrollment import require_collection_support as require_windows_support

        require_windows_support(python)
        return
    if sys.platform != "linux":
        raise slots.HealthError("native-health-job-unsupported")
    environment = python.parent.parent
    packages = [
        path / "site-packages" / "agent_machines"
        for path in (environment / "lib").glob("python[0-9]*")
        if re.fullmatch(r"python3\.[0-9]+", path.name) is not None
        and (path / "site-packages" / "agent_machines").is_dir()
    ]
    if len(packages) != 1:
        raise slots.HealthError("installed-job-collection-unsupported")
    package = packages[0]
    if package.is_symlink() or not package.resolve().is_relative_to(environment.resolve()):
        raise slots.HealthError("installed-job-collection-unsupported")
    read = cast(Callable[..., tuple[object, object]], slots._read)
    declaration, _ = read(
        package / "health-collection-v1.json", lambda value: value, maximum=4096,
    )
    if (
        type(declaration) is not dict
        or declaration.get("schema") != "agent-machines.health.collection"
        or type(declaration.get("version")) is not int or declaration["version"] != 1
        or type(declaration.get("operations")) is not list
        or not all(type(item) is str for item in declaration["operations"])
        or not {"preview", "apply", "installed-job"}.issubset(declaration["operations"])
    ):
        raise slots.HealthError("installed-job-collection-unsupported")
