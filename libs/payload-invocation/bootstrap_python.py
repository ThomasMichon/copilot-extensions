"""Publish inert bootstrap requests without changing application launchers."""
from __future__ import annotations

from importlib import import_module
from pathlib import Path
import re
from typing import Any


def expected_files(manifest: Path, data: dict[str, Any]) -> dict[Path, str]:
    destination = manifest.parent / "scripts/bootstrap-python-version.txt"
    regular = destination.is_file()
    occupied = regular or destination.is_symlink()
    if occupied:
        declared_inputs = [data.get("catalogGate")]
        dispatchers = data.get("payloadDispatcher")
        if isinstance(dispatchers, dict):
            declared_inputs.extend(dispatchers.values())
        for value in declared_inputs:
            if not isinstance(value, str) or not value:
                continue
            source = manifest.parent / value
            if source == destination or (regular and source.is_file() and source.samefile(destination)):
                if "bootstrapPython" in data:
                    raise ValueError(f"{manifest}: bootstrapPython output conflicts with a declared input")
                return {}
    if "bootstrapPython" not in data:
        if occupied:
            raise ValueError(f"{manifest}: obsolete bootstrap Python request; remove {destination.name}")
        return {}
    version = data["bootstrapPython"]
    if (
        not isinstance(version, str) or len(version) > 32
        or not re.fullmatch(r"3\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version)
    ):
        raise ValueError(f"{manifest}: bootstrapPython must be an exact stable CPython version")
    try:
        tomllib = import_module("tomllib")
    except ModuleNotFoundError:
        tomllib = import_module("tomli")
    from packaging.specifiers import SpecifierSet

    project = manifest.parent / "pyproject.toml"
    if project.is_symlink() or not project.is_file():
        raise ValueError(f"{manifest}: bootstrapPython requires an ordinary pyproject.toml")
    try:
        metadata = tomllib.loads(project.read_text(encoding="utf-8"))
        declaration = metadata.get("project")
        requirement = declaration.get("requires-python") if isinstance(declaration, dict) else None
        if not isinstance(requirement, str) or not requirement.strip():
            raise ValueError("project.requires-python must be declared")
        if not SpecifierSet(requirement).contains(version, prereleases=False):
            raise ValueError(f"bootstrapPython {version} does not satisfy requires-python {requirement}")
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"{manifest}: invalid bootstrap Python project: {exc}") from exc
    return {destination: f"cpython@{version}\n"}
