#!/usr/bin/env python3
"""Generate inert third-party release-lock inputs; never resolve or install them."""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections import deque
from pathlib import Path

from packaging.markers import Environment, Marker
from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from dependency_age import contained_path, manifests
from python_project_inventory import runtime_roots
from seasoned_public_feed import DEFAULT_MIN_AGE_DAYS

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path("bundle") / "pyproject.toml"
GROUPS = ("core", "index-service", "index-engine")


class InputError(ValueError):
    """The candidate inputs would omit or misrepresent runtime dependencies."""


def _read(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _catalog(root: Path) -> dict[str, tuple[Path, dict]]:
    result = {}
    for manifest in manifests(root):
        manifest = contained_path(manifest, root)
        data = _read(manifest)
        if "project" not in data:
            raise InputError(f"{manifest.relative_to(root)}: project metadata is required")
        project = data["project"]
        if not isinstance(project, dict):
            raise InputError(f"{manifest.relative_to(root)}: project must be a table")
        name = project.get("name")
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", name,
        ):
            raise InputError(f"{manifest.relative_to(root)}: invalid distribution name")
        name = canonicalize_name(name)
        if name in result:
            raise InputError(f"ambiguous first-party distribution: {name}")
        _runtime_metadata(project, name)
        result[name] = (manifest, data)
    return result


def _requirements(project: dict, extra: str, name: str) -> list[str]:
    if extra:
        extras = project.get("optional-dependencies", {})
        if not isinstance(extras, dict):
            raise InputError(f"{name}: optional dependencies must be a table")
        matches = [value for key, value in extras.items() if canonicalize_name(key) == extra]
        if len(matches) != 1:
            raise InputError(f"{name}: unknown or ambiguous extra {extra}")
        requirements = matches[0]
    else:
        requirements = project.get("dependencies", [])
    if not isinstance(requirements, list) or any(not isinstance(r, str) for r in requirements):
        raise InputError(f"{name}: runtime dependencies must be strings")
    return requirements


def _runtime_metadata(project: dict, name: str) -> tuple[str, dict[str, frozenset[str]]]:
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list) or any(not isinstance(value, str) for value in dynamic):
        raise InputError(f"{name}: dynamic fields must be a list of names")
    if set(dynamic) & {"dependencies", "optional-dependencies", "requires-python"}:
        raise InputError(f"{name}: dynamic runtime metadata is unsupported")
    extras = project.get("optional-dependencies", {})
    if not isinstance(extras, dict):
        raise InputError(f"{name}: optional dependencies must be a table")
    result = {"": frozenset(_requirements(project, "", name))}
    for extra in extras:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", extra):
            raise InputError(f"{name}: invalid extra name")
        normalized = canonicalize_name(extra)
        result[normalized] = frozenset(_requirements(project, normalized, name))
    python = project.get("requires-python", "")
    if not isinstance(python, str):
        raise InputError(f"{name}: requires-python must be a string")
    try:
        return str(SpecifierSet(python)), result
    except InvalidSpecifier as exc:
        raise InputError(f"{name}: invalid requires-python") from exc


def _source_mapping(
    root: Path, manifest: Path, data: dict, name: str, *, first_party: bool = True,
) -> Path | None:
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    if not isinstance(sources, dict):
        raise InputError(f"{manifest.relative_to(root)}: sources must be a table")
    matches = [entry for key, entry in sources.items() if canonicalize_name(key) == name]
    if not matches:
        return
    if len(matches) != 1 or not isinstance(matches[0], dict):
        raise InputError(f"{name}: conditional or ambiguous source mapping is unsupported")
    source = matches[0]
    if set(source) - {"path", "editable"} or not isinstance(source.get("path"), str):
        raise InputError(f"{name}: only attributed first-party path sources can be stripped")
    if "editable" in source and not isinstance(source["editable"], bool):
        raise InputError(f"{name}: editable must be boolean")
    target = contained_path(manifest.parent / source["path"] / "pyproject.toml", root)
    if not first_party and not target.is_file():
        raise InputError(f"{name}: local dependency is absent from canonical inventory")
    selected = _read(target)["project"]
    if canonicalize_name(selected["name"]) != name:
        raise InputError(f"{name}: local source distribution identity differs")
    return target


def _roots(
    root: Path, catalog: dict, groups=GROUPS,
) -> dict[str, list[tuple[str, str, frozenset[str]]]]:
    if (
        not isinstance(groups, (tuple, list)) or not groups
        or any(not isinstance(group, str) or group not in GROUPS for group in groups)
        or len(set(groups)) != len(groups)
    ):
        raise InputError("groups must be nonempty, unique runtime environment names")
    selected = runtime_roots(root, catalog)
    if any(not selected.get(group) for group in groups):
        if set(groups) == set(GROUPS):
            raise InputError("all three runtime environment roots must be present")
        raise InputError("selected runtime environment roots must be present")
    return {group: selected[group] for group in GROUPS if group in groups}


def _environment(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value.keys() != Environment.__annotations__.keys():
        raise InputError("target environment must contain exactly the standard marker fields")
    if any(
        not isinstance(item, str) or not item or item != item.strip()
        for item in value.values()
    ):
        raise InputError("target environment values must be nonempty trimmed strings")
    try:
        versions = {
            key: Version(value[key])
            for key in ("python_version", "python_full_version", "implementation_version")
        }
    except InvalidVersion as exc:
        raise InputError("target environment has an invalid Python version") from exc
    if (
        not re.fullmatch(r"\d+\.\d+", value["python_version"])
        or len(versions["python_full_version"].release) < 3
        or versions["python_full_version"].release[:2] != versions["python_version"].release
    ):
        raise InputError("target Python major/minor disagrees with full version")
    platforms = {
        "win32": ("nt", "Windows"), "linux": ("posix", "Linux"),
        "darwin": ("posix", "Darwin"),
    }
    expected = platforms.get(value["sys_platform"])
    if (
        expected is not None and (value["os_name"], value["platform_system"]) != expected
        or any(
            value["platform_system"] == system and value["sys_platform"] != platform
            for platform, (_os, system) in platforms.items()
        )
        or value["os_name"] == "nt" and value["sys_platform"] != "win32"
    ):
        raise InputError("target platform identity disagrees")
    if (
        (value["implementation_name"] == "cpython")
        != (value["platform_python_implementation"] == "CPython")
        or value["implementation_name"] == "cpython"
        and versions["implementation_version"] != versions["python_full_version"]
    ):
        raise InputError("target CPython implementation identity disagrees")
    return dict(value)


def _active(conditions: frozenset[str], environment: dict[str, str] | None) -> bool:
    return environment is None or all(
        Marker(condition).evaluate(environment=environment) for condition in conditions
    )


def _inactive_source(data: dict, name: str, environment: dict[str, str] | None) -> bool:
    if environment is None:
        return False
    metadata = _runtime_metadata(data["project"], data["project"]["name"])
    declarations = [
        requirement
        for values in metadata[1].values()
        for value in values
        if canonicalize_name((requirement := Requirement(value)).name) == name
    ]
    return bool(declarations) and all(
        requirement.marker is not None
        and not re.search(r"\b(extra|extras|dependency_groups)\b", str(requirement.marker))
        and not requirement.marker.evaluate(environment=environment)
        for requirement in declarations
    )


def _resolver_settings(root: Path, manifest: Path, data: dict, documents: dict) -> None:
    for directory in (manifest.parent, *manifest.parent.parents):
        if not directory.is_relative_to(root):
            break
        configuration = directory / "uv.toml"
        if configuration.exists() or configuration.is_symlink():
            raise InputError(f"{directory.relative_to(root)}: uv.toml cannot be preserved")
        ancestor = directory / "pyproject.toml"
        if ancestor == manifest:
            settings = data
        elif ancestor.exists():
            settings = _read(contained_path(ancestor, root))
        else:
            continue
        uv = settings.get("tool", {}).get("uv", {})
        if not isinstance(uv, dict):
            raise InputError(f"{ancestor.relative_to(root)}: native UV settings must be a table")
        allowed = {"exclude-newer"}
        if ancestor == manifest or ancestor in documents:
            allowed.add("sources")
        if set(uv) - allowed:
            raise InputError(f"{ancestor.relative_to(root)}: resolver settings cannot be preserved")
        if not isinstance(uv.get("sources", {}), dict):
            raise InputError(f"{ancestor.relative_to(root)}: sources must be a table")
        if "exclude-newer" in uv and uv["exclude-newer"] != f"{DEFAULT_MIN_AGE_DAYS} days":
            raise InputError(f"{ancestor.relative_to(root)}: exclude-newer cannot be preserved")


def collect_inputs(
    root: Path, *, groups=GROUPS, target_environment: dict[str, str] | None = None,
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Project selected runtime roots, optionally for an explicit complete marker context."""
    root = root.resolve()
    environment = _environment(target_environment)
    catalog = _catalog(root)
    documents = {manifest: data for manifest, data in catalog.values()}
    selected_groups = _roots(root, catalog, groups)
    groups, python_ranges = {}, {}
    for group, roots in selected_groups.items():
        pending = deque((name, extra, inherited, catalog[name][0]) for name, extra, inherited in roots)
        seen = set()
        source_documents = set()
        external = set()
        external_names = set()
        python = set()
        conditional_python = set()
        while pending:
            name, extra, inherited, manifest = pending.popleft()
            if not _active(inherited, environment):
                continue
            key = (name, extra, inherited, manifest)
            if key in seen:
                continue
            seen.add(key)
            if manifest not in documents:
                documents[manifest] = _read(manifest)
            data = documents[manifest]
            if canonicalize_name(data["project"]["name"]) != name:
                raise InputError(f"{name}: local source distribution identity differs")
            metadata = _runtime_metadata(data["project"], name)
            if metadata != _runtime_metadata(catalog[name][1]["project"], name):
                raise InputError(f"{name}: selected source runtime metadata differs from canonical")
            _resolver_settings(root, catalog[name][0], catalog[name][1], documents)
            if manifest != catalog[name][0]:
                _resolver_settings(root, manifest, data, documents)
            source_documents.add(manifest)
            source_documents.add(catalog[name][0])
            if environment is not None and not SpecifierSet(metadata[0]).contains(
                environment["python_full_version"], prereleases=True,
            ):
                raise InputError(f"{name}: target Python is outside the runtime range")
            if inherited and environment is None:
                conditional_python.add((name, metadata[0]))
            else:
                python.add(metadata[0])
            for value in _requirements(data["project"], extra, name):
                requirement = Requirement(value)
                dependency = canonicalize_name(requirement.name)
                conditions = inherited
                if requirement.marker:
                    marker = str(requirement.marker)
                    if re.search(r"\b(extra|extras|dependency_groups)\b", marker):
                        raise InputError(f"{name}: context-dependent extra/group marker is unsupported")
                    conditions = conditions | {marker}
                if not _active(conditions, environment):
                    continue
                if requirement.url:
                    raise InputError(f"{name}: direct dependency references are unsupported")
                selected = _source_mapping(
                    root, manifest, data, dependency, first_party=dependency in catalog,
                )
                if dependency in catalog:
                    selected = selected or catalog[dependency][0]
                    pending.append((dependency, "", conditions, selected))
                    pending.extend(
                        (dependency, canonicalize_name(extra_name), conditions, selected)
                        for extra_name in sorted(requirement.extras)
                    )
                    continue
                if selected is not None:
                    raise InputError(f"{dependency}: local dependency is absent from canonical inventory")
                external_names.add(dependency)
                requirement.name = dependency
                requirement.marker = None
                rendered = str(requirement)
                if conditions:
                    rendered += "; " + " and ".join(f"({condition})" for condition in sorted(conditions))
                external.add(rendered)
        # Undeclared mappings may govern third-party transitives; absence is not inactivity.
        for manifest in sorted(source_documents):
            data = documents[manifest]
            sources = data.get("tool", {}).get("uv", {}).get("sources", {})
            dependencies = {canonicalize_name(name) for name in sources} - catalog.keys()
            for dependency in sorted(dependencies):
                if dependency not in external_names and _inactive_source(data, dependency, environment):
                    continue
                selected = _source_mapping(
                    root, manifest, data, dependency, first_party=False,
                )
                if selected is not None:
                    raise InputError(f"{dependency}: local dependency is absent from canonical inventory")
        groups[group] = sorted(external)
        unconditional_python = SpecifierSet(",".join(sorted(python)))
        for name, value in sorted(conditional_python):
            # Group metadata cannot express a marker-dependent Python intersection.
            if not set(SpecifierSet(value)).issubset(set(unconditional_python)):
                raise InputError(f"{name}: conditional first-party Python range cannot be flattened")
        python_ranges[group] = str(unconditional_python)
    return groups, python_ranges


def _python_floor(value: str) -> Version:
    floors = [
        Version(specifier.version.removesuffix(".*"))
        for specifier in SpecifierSet(value)
        if specifier.operator in (">=", ">", "==", "~=", "===")
    ]
    if not floors:
        raise InputError("runtime Python range needs an attributable lower bound")
    return max(floors)


def render(root: Path, *, groups=GROUPS, target_environment: dict[str, str] | None = None) -> str:
    """Render inert inputs; target attribution is not native qualification or approval."""
    environment = _environment(target_environment)
    groups, python_ranges = collect_inputs(root, groups=groups, target_environment=environment)
    python_floor = min(_python_floor(value) for value in python_ranges.values())
    lines = [
        "# Generated by tools/bundle_lock.py generate; candidate inputs, not an approved lock.",
        "[project]", 'name = "copilot-extensions-bundle-inputs"', 'version = "0.0.0"',
        f'requires-python = ">={python_floor}"', "", "[tool.uv]", "package = false",
        f'exclude-newer = "{DEFAULT_MIN_AGE_DAYS} days"', "",
        "[tool.uv.dependency-groups]",
        *[
            f"{group} = {{ requires-python = {json.dumps(value)} }}"
            for group, value in python_ranges.items()
        ],
        "",
        "[dependency-groups]",
    ]
    for group, requirements in groups.items():
        lines.append(f"{group} = [")
        lines.extend(f"    {json.dumps(value)}," for value in requirements)
        lines.append("]")
    if environment is not None:
        lines.extend(["", "[tool.bundle-inputs.target-environment]"])
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in sorted(environment.items()))
    return "\n".join(lines) + "\n"


def check(root: Path) -> None:
    expected = render(root)
    output = root / OUTPUT
    if not output.is_file() or output.read_text(encoding="utf-8") != expected:
        raise InputError("bundle inputs are stale; run python tools/bundle_lock.py generate")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("generate", "check"))
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.operation == "check":
            check(root)
        else:
            content = render(root)
            output = root / OUTPUT
            output.parent.mkdir(parents=True, exist_ok=True)
            output = contained_path(output, root)
            output.write_text(content, encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"Bundle inputs refused: {exc}", file=sys.stderr)
        return 1
    print(f"Bundle inputs {args.operation}: OK (candidate inputs only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
