"""Inventory declared Python source owners; never construct or qualify a runtime."""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tomllib

class InventoryError(ValueError):
    """The canonical source inventory cannot be read unambiguously."""


def _relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
    ):
        raise InventoryError(f"invalid repository-relative path: {value!r}")
    return path.as_posix()


def _source_file(root: Path, relative: str) -> Path:
    path = root.joinpath(*PurePosixPath(_relative(relative)).parts)
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise InventoryError(f"missing or escaping source file: {relative}")
    return path


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise InventoryError(f"{label} must be an array of strings")
    return value


def _distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _standalone_consumers(root: Path) -> list[str]:
    path = _source_file(root, "tools/standalone_consumers.py")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assignments = [
            node.value
            for node in tree.body
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "STANDALONE_CONSUMERS" for target in node.targets)
            )
            or (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "STANDALONE_CONSUMERS"
            )
        ]
        stores = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "STANDALONE_CONSUMERS" and isinstance(node.ctx, ast.Store)
        ]
        if len(assignments) != 1 or len(stores) != 1:
            raise InventoryError("standalone roster needs exactly one literal declaration")
        value = ast.literal_eval(assignments[0])
    except (SyntaxError, TypeError, ValueError) as exc:
        raise InventoryError(f"invalid standalone roster declaration: {exc}") from exc
    if not isinstance(value, tuple) or any(not isinstance(item, str) for item in value):
        raise InventoryError("standalone roster must be an immutable literal tuple of paths")
    consumers = [_relative(item) for item in value]
    if len(set(consumers)) != len(consumers):
        raise InventoryError("duplicate standalone owner")
    return consumers


def _project(root: Path, path: str, kind: str, owner: str) -> dict:
    with _source_file(root, path).open("rb") as stream:
        data = tomllib.load(stream)
    project = data.get("project", {})
    if not isinstance(project, dict):
        raise InventoryError(f"{path}: project must be a table")
    name = project.get("name")
    python = project.get("requires-python")
    if not isinstance(name, str) or not name.strip():
        raise InventoryError(f"{path}: missing project.name")
    if not isinstance(python, str) or not python.strip():
        raise InventoryError(f"{path}: missing project.requires-python")
    extras = project.get("optional-dependencies", {})
    scripts = project.get("scripts", {})
    build = data.get("build-system", {})
    if not isinstance(extras, dict) or not isinstance(scripts, dict) or not isinstance(build, dict):
        raise InventoryError(f"{path}: invalid dependency, script or build table")
    if any(not isinstance(value, str) for value in scripts.values()):
        raise InventoryError(f"{path}: script entry points must be strings")
    dependencies = _strings(project.get("dependencies", []), f"{path}: dependencies")
    for extra, requirements in extras.items():
        _strings(requirements, f"{path}: optional-dependencies.{extra}")
    build_requires = _strings(build.get("requires", []), f"{path}: build-system.requires")
    return {
        "owner": owner,
        "kind": kind,
        "project": path,
        "distribution": name,
        "requires_python": python,
        "runtime_requirement_count": len(dependencies),
        "build_requirement_count": len(build_requires),
        "optional_dependency_groups": sorted(extras),
        "script_entry_points": dict(sorted(scripts.items())),
    }


def inventory(root: Path, tracked_projects: list[str]) -> dict:
    """Classify source owners without inferring environments from products or extras."""
    projects = [_relative(path) for path in tracked_projects]
    if len(set(projects)) != len(projects):
        raise InventoryError("duplicate tracked project path")
    if any(PurePosixPath(path).name != "pyproject.toml" for path in projects):
        raise InventoryError("tracked project paths must name pyproject.toml")
    for path in projects:
        _source_file(root, path)
    tracked = set(projects)
    marketplace = ".github/plugin/marketplace.json"
    data = json.loads(_source_file(root, marketplace).read_text(encoding="utf-8"))
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, list):
        raise InventoryError(f"{marketplace}: plugins must be an array")
    owners: list[dict] = []
    classified: set[str] = set()
    project_locations: set[Path] = set()
    identities: set[str] = set()
    sources: set[str] = set()
    standalone_consumers = _standalone_consumers(root)

    def claim_project(path: str, kind: str, owner: str, scope: str) -> None:
        location = _source_file(root, path).resolve()
        owner_root = root.joinpath(*PurePosixPath(scope).parts).resolve()
        if not location.is_relative_to(owner_root):
            raise InventoryError(f"Python project escapes its owning source root: {path}")
        if path in classified or location in project_locations:
            raise InventoryError(f"multiply owned Python project: {path}")
        owners.append(_project(root, path, kind, owner))
        classified.add(path)
        project_locations.add(location)

    for plugin in plugins:
        if not isinstance(plugin, dict):
            raise InventoryError(f"{marketplace}: invalid plugin entry")
        name, source = plugin.get("name"), plugin.get("source")
        if not isinstance(name, str) or not name or not isinstance(source, str):
            raise InventoryError(f"{marketplace}: plugin needs a name and local source")
        source = _relative(source)
        if name in identities or source in sources:
            raise InventoryError(f"{marketplace}: duplicate plugin identity or source")
        identities.add(name)
        sources.add(source)
        manifest_path = f"{source}/plugin.json"
        manifest = json.loads(_source_file(root, manifest_path).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("name") != name:
            raise InventoryError(f"{manifest_path}: marketplace identity mismatch")
        path = f"{source}/pyproject.toml"
        exists = root.joinpath(*PurePosixPath(path).parts).is_file()
        if exists and path not in tracked:
            raise InventoryError(f"untracked runtime declaration: {path}")
        if path not in tracked:
            if manifest.get("runtimeScope") not in (None, "none"):
                raise InventoryError(f"missing runtime project: {path}")
        else:
            if manifest.get("runtimeScope") == "none":
                raise InventoryError(f"{manifest_path}: runtimeScope none has a Python project")
            claim_project(path, "runtime-plugin", name, source)
        additional = _strings(
            manifest.get("additionalPythonProjects", []),
            f"{manifest_path}: additionalPythonProjects",
        )
        for relative in additional:
            relative = _relative(relative)
            path = f"{source}/{relative}"
            if PurePosixPath(relative).name != "pyproject.toml" or path not in tracked:
                raise InventoryError(f"missing additional Python project: {path}")
            claim_project(path, "plugin-python-project", f"{name}:{relative}", source)
    for component in standalone_consumers:
        path = f"{_relative(component)}/pyproject.toml"
        if path not in tracked:
            raise InventoryError(f"missing standalone project: {path}")
        claim_project(path, "standalone-product", component, component)
    library_inputs = sorted(
        path
        for path in tracked - classified
        if len(PurePosixPath(path).parts) == 3 and PurePosixPath(path).parts[0] == "libs"
    )
    libraries = {
        path: _project(root, path, "shared-library", PurePosixPath(path).parts[1])
        for path in library_inputs
    }
    vendored_inputs = []
    consumers = sources | set(standalone_consumers)
    remaining = tracked - classified - set(library_inputs)
    for path in sorted(remaining):
        for consumer in sorted(consumers):
            prefix = f"{consumer}/libs/"
            if not path.startswith(prefix):
                continue
            suffix = PurePosixPath(path[len(prefix):])
            if len(suffix.parts) != 2:
                continue
            canonical = f"libs/{suffix.parts[0]}/pyproject.toml"
            if canonical not in library_inputs:
                continue
            original = libraries[canonical]
            copy = _project(root, path, "vendored-library", suffix.parts[0])
            if _distribution_name(original["distribution"]) != _distribution_name(copy["distribution"]):
                raise InventoryError(f"vendored library identity mismatch: {path}")
            vendored_inputs.append({"project": path, "canonical": canonical})
            break
    unclassified = sorted(remaining - {item["project"] for item in vendored_inputs})
    return {
        "schema": "copilot-extensions.construction-source-inventory",
        "version": 1,
        "marketplace_entries": len(plugins),
        "owners": sorted(owners, key=lambda owner: (owner["kind"], owner["owner"])),
        "shared_library_inputs": library_inputs,
        "vendored_library_inputs": vendored_inputs,
        "unclassified_python_projects": unclassified,
        "source_owner_inventory_complete": not unclassified,
        "runtime_qualification": "not-performed",
        "remaining_classification": [
            "actual construction environments and product composition",
            "selected production extras and independent sibling profiles",
            "canonical construction and installed-launch actions",
            "native OS, interpreter architecture and ABI matrix",
            "bootstrap and isolated-build closure",
        ],
    }


def tracked_projects(root: Path) -> list[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "pyproject.toml", "**/pyproject.toml"],
        cwd=root,
        env=env,
        capture_output=True,
        check=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return [path for path in result.stdout.decode("utf-8").split("\0") if path]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        report = inventory(args.root, tracked_projects(args.root))
    except (InventoryError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"construction source inventory failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["source_owner_inventory_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
