"""Publish inert bootstrap requests without changing application launchers."""
from __future__ import annotations

from importlib import import_module
from pathlib import Path
import re
from typing import Any


def expected_files(manifest: Path, data: dict[str, Any]) -> dict[Path, str]:
    if "bootstrapPython" not in data:
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
    return {manifest.parent / "scripts/bootstrap-python-version.txt": f"cpython@{version}\n"}
