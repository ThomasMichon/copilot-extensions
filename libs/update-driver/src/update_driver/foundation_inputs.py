"""Fixed standalone foundation inputs; no plugin or arbitrary package recipes."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

PACKAGES = {
    "update-driver": ("agent-update-driver", {"agent-procutil", "agent-single-instance-lease"}),
    "agent-procutil": ("agent-procutil", set()),
    "single-instance-lease": ("agent-single-instance-lease", set()),
}
SUPPORT = (
    "installer_engine.py", "installer_engine_process.py", "installer_dependency_policy.py",
    "installer_dependency_config.py", "runtime_launcher.py", "versioned_runtime.py",
    "versioned_runtime_leases.py", "versioned_runtime_construction.py",
    "versioned_runtime_source.py", "versioned_runtime_publication.py",
)
GENERATED = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist",
    "node_modules",
}


class FoundationInputError(RuntimeError):
    """The selected source does not supply the fixed foundation closure."""


@dataclass(frozen=True)
class FoundationInputs:
    plugin: Path
    packages: tuple[Path, ...]
    files: tuple[Path, ...]
    digest: str


def _ordinary(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise FoundationInputError("foundation inputs must be ordinary unlinked entries")


def inspect(plugin: Path) -> FoundationInputs:
    """Inspect one fixed sibling closure, equally on dev and a materialized payload."""
    import tomllib

    plugin = Path(plugin)
    if not plugin.is_absolute() or plugin.resolve() != plugin or plugin.name != "update-driver":
        raise FoundationInputError("foundation requires the exact unlinked update-driver source")
    common = plugin.parent
    files = []
    packages = []
    for directory, (name, dependencies) in PACKAGES.items():
        package = common / directory
        _ordinary(package, directory=True)
        manifest = package / "pyproject.toml"
        _ordinary(manifest)
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        project = data.get("project", {})
        if project.get("name") != name or set(project.get("dependencies", [])) != dependencies:
            raise FoundationInputError("foundation runtime dependency closure differs from its fixed recipe")
        sources = data.get("tool", {}).get("uv", {}).get("sources", {})
        if set(sources) != dependencies:
            raise FoundationInputError("foundation requires exactly its declared local dependency sources")
        for dependency, source in sources.items():
            target = next(key for key, value in PACKAGES.items() if value[0] == dependency)
            if (
                not isinstance(source, dict) or set(source) - {"path", "editable"}
                or source.get("path") != f"../{target}"
                or ("editable" in source and type(source["editable"]) is not bool)
            ):
                raise FoundationInputError("foundation dependency source is not its fixed sibling")
        packages.append(package)

        def scan_error(error: OSError) -> None:
            raise error

        for parent, children, names in os.walk(package, onerror=scan_error, followlinks=False):
            base = Path(parent)
            for child in list(children):
                _ordinary(base / child, directory=True)
                if child in GENERATED or child.endswith((".egg-info", ".dist-info")):
                    children.remove(child)
            for filename in names:
                path = base / filename
                _ordinary(path)
                if not filename.endswith((".pyc", ".pyo")):
                    files.append(path)
    for filename in SUPPORT:
        _ordinary(plugin / "scripts" / filename)
    files.sort(key=lambda path: path.relative_to(common).as_posix())
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(common).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii") + b"\n")
    return FoundationInputs(plugin, tuple(sorted(packages)), tuple(files), digest.hexdigest())


def check_health() -> None:
    """Installed, side-effect-free foundation capability and native-asset check."""
    from importlib.resources import files

    import agent_procutil
    import single_instance_lease

    from . import core, foundation_state
    from .participant import MAX_FRAME

    if core.SCHEMA != 2 or foundation_state.VERSION != 1 or MAX_FRAME != 16384:
        raise FoundationInputError("unsupported foundation execution protocol")
    for package, names in (
        (agent_procutil, ("native_daemon.ps1", "native_owned.ps1")),
        (single_instance_lease, ("operation.ps1",)),
    ):
        if any(not files(package).joinpath(name).is_file() for name in names):
            raise FoundationInputError("foundation is missing native custody assets")
