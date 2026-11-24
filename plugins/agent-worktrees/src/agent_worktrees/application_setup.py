"""Compatibility publication for the installed Source application's setup."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from . import installer, output


def _deploy_runtime_resolvers(root: Path) -> bool:
    sources = [
        installer.scripts_dir() / name
        for name in ("resolve-runtime.ps1", "resolve-runtime.sh")
    ]
    if any(not source.is_file() for source in sources):
        output.err("Source application is missing its runtime resolvers.")
        return False
    directory = root.resolve() / "bin"
    if directory.exists() and directory.resolve() != directory:
        output.err("Source application runtime resolver directory cannot be indirect.")
        return False
    directory.mkdir(parents=True, exist_ok=True)
    for source in sources:
        destination = directory / source.name
        content = source.read_bytes()
        if destination.is_file() and destination.read_bytes() == content:
            continue
        temporary = directory / f".{source.name}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o755 if source.suffix == ".sh" else 0o644)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return True


def setup_application() -> int:
    """Publish compatibility readers before the existing registration refresh."""
    root = installer._application_root()
    if root is None:
        return installer.setup_application()
    with installer._binstub_lock("__registries__"):
        installer._projects_snapshot_for_mutation()
        if not _deploy_runtime_resolvers(root):
            return 1
        return installer.setup_application()
