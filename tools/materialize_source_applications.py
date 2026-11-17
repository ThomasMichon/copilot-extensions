"""Emit self-contained application source projects after canonical materialization."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import sys
from collections.abc import Callable
from typing import Any, cast

import materialize_installer_engine as assets

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

_DECLARATION = "application-source.json"


def retain_outputs(root: Path, selected: tuple[str, ...]) -> tuple[str, ...]:
    """Keep generated outputs with their selected source at local promotion."""
    if "worktree-manager" in selected and (root / "apps/worktree-manager").is_dir():
        return tuple(sorted({*selected, "apps/worktree-manager"}))
    return selected


def _table(text: str, name: str, entries: dict[str, Any]) -> str:
    pattern = re.compile(
        r"^\[" + re.escape(name) + r"\][^\n]*\n.*?(?=^\[|\Z)", re.MULTILINE | re.DOTALL,
    )

    def value(item: Any) -> str:
        if isinstance(item, dict):
            return "{ " + ", ".join(
                json.dumps(key) + " = " + value(field) for key, field in item.items()
            ) + " }"
        if isinstance(item, list):
            return "[" + ", ".join(value(field) for field in item) + "]"
        if isinstance(item, (str, bool)):
            return json.dumps(item, ensure_ascii=True)
        raise ValueError("unsupported application project table value")

    replacement = (
        "[" + name + "]\n"
        + "".join(json.dumps(key) + " = " + value(item) + "\n" for key, item in entries.items())
        + "\n"
    ) if entries else ""
    result, count = pattern.subn(lambda _match: replacement, text)
    if count > 1:
        raise ValueError("ambiguous application project table")
    return result if count else text.rstrip() + "\n\n" + replacement


def _project_manifest(source: Path) -> str:
    from standalone_build import embedded_projects as select_embedded

    text = (source / "pyproject.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    embedded = cast(Callable[[Path], dict[str, tuple[Path, dict[str, Any]]]], select_embedded)(source)
    groups = dict(data.get("dependency-groups", {}))
    groups.pop("first-party-core", None)
    text = _table(text, "dependency-groups", groups)
    mappings = dict(data.get("tool", {}).get("uv", {}).get("sources", {}))
    for name in embedded:
        mappings.pop(name)
    text = _table(text, "tool.uv.sources", mappings)
    package_data = dict(data.get("tool", {}).get("setuptools", {}).get("package-data", {}))
    for directory, metadata in embedded.values():
        for package, patterns in metadata.get("tool", {}).get("setuptools", {}).get("package-data", {}).items():
            package_data[package] = sorted(set(package_data.get(package, ())) | set(patterns))
        roots = [path for path in (directory / "src").iterdir() if (path / "__init__.py").is_file()]
        if len(roots) != 1:
            raise ValueError("embedded application library has no unique package")
        package = roots[0].name
        package_data[package] = sorted(set(package_data.get(package, ())) | {"LICENSE"})
    text = _table(text, "tool.setuptools.package-data", package_data)
    tomllib.loads(text)
    return text


def materialize(root: Path) -> list[str]:
    """Produce the opted-in Manager source project without building or installing."""
    source = root / "worktree-manager"
    declaration = source / _DECLARATION
    if not declaration.exists() and not declaration.is_symlink():
        return []
    from governed_feed_trust import ArtifactBuildError
    from standalone_python_artifacts import injected_resources

    try:
        problem = assets.derived_path_problem(declaration, root, required=True)
        if problem:
            raise ValueError(problem)
        if json.loads(declaration.read_text(encoding="utf-8")) != {
            "schema": "copilot-extensions.application-source", "version": 1,
            "name": "worktree-manager",
        }:
            raise ValueError("unsupported application source declaration")
        destination = root / "apps" / "worktree-manager"
        if destination.exists() or destination.is_symlink():
            raise ValueError("application source output already exists")
        if problem := assets.derived_path_problem(destination / "pyproject.toml", root, required=False):
            raise ValueError(problem)
        from uv_editable_ref import materialized_local_source_problems

        if problems := materialized_local_source_problems(source):
            raise ValueError("; ".join(problems))
        if not (source / "src/worktree_manager/__init__.py").is_file():
            raise ValueError("application source package is missing")
        resources = injected_resources("worktree-manager", root)
        manifest = _project_manifest(source)
        members = {
            path.relative_to(source).as_posix(): path
            for path in (source / "src").rglob("*") if path.is_file()
        }
        members.update({
            name: source / name for name in ("README.md", "LICENSE", "setup.py")
        })
        from python_project_inventory import contained_path

        mappings = tomllib.loads(manifest).get("tool", {}).get("uv", {}).get("sources", {})
        for mapping in mappings.values():
            for entry in mapping if isinstance(mapping, list) else [mapping]:
                if isinstance(entry, dict) and "path" in entry:
                    local = contained_path(source / entry["path"], source)
                    for path in local.rglob("*") if local.is_dir() else [local]:
                        if path.is_file():
                            members[path.relative_to(source).as_posix()] = path
        for name, path in resources.items():
            relative = "src/" + name
            if relative in members and members[relative].read_bytes() != path.read_bytes():
                raise ValueError("included application source resource differs from its owner")
            members[relative] = path
        members["_standalone_runtime_build.py"] = root / "libs/versioned-runtime/standalone_build.py"
        for path in members.values():
            if problem := assets.derived_path_problem(path, root, required=True):
                raise ValueError(problem)
        destination.mkdir(parents=True)
        for name, path in sorted(members.items()):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        (destination / "pyproject.toml").write_text(manifest, encoding="utf-8", newline="\n")
        (destination / "MANIFEST.in").write_text(
            "include _standalone_runtime_build.py\nrecursive-include src *\n",
            encoding="utf-8", newline="\n",
        )
        if problems := materialized_local_source_problems(destination):
            raise ValueError("; ".join(problems))
        return [f"OK   {destination} <- {source} (embedded application source)"]
    except (OSError, ValueError, RuntimeError, ArtifactBuildError) as error:
        return [f"SKIP {declaration}: {error}"]
