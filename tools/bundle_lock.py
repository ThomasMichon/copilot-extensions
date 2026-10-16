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

from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from dependency_age import contained_path, manifests
from seasoned_public_feed import DEFAULT_MIN_AGE_DAYS
from standalone_consumers import STANDALONE_CONSUMERS

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path("bundle") / "pyproject.toml"
GROUPS = ("core", "index-service", "index-engine")
OPTIONAL_CORE = {"agent-dispatch": ("mcp",), "agent-containers": ("provider-exec",)}
PORTABLE_EXTRA = 'sys_platform != "win32" or platform_machine != "ARM64"'


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


def _source_mapping(root: Path, manifest: Path, data: dict, name: str) -> Path | None:
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
    selected = _read(target)["project"]
    if canonicalize_name(selected["name"]) != name:
        raise InputError(f"{name}: local source distribution identity differs")
    return target


def _roots(root: Path, catalog: dict) -> dict[str, list[tuple[str, str, frozenset[str]]]]:
    groups = {group: [] for group in GROUPS}
    special = {
        Path("services") / "index": "index-service",
        Path("plugins") / "agent-index" / "server": "index-engine",
    }
    standalone = {Path(value) for value in STANDALONE_CONSUMERS}
    for name, (manifest, _data) in sorted(catalog.items()):
        relative = manifest.parent.relative_to(root)
        if relative in special:
            group = special[relative]
        elif relative in standalone or (len(relative.parts) == 2 and relative.parts[0] == "plugins"):
            group = "core"
        else:
            continue
        groups[group].append((name, "", frozenset()))
        extras = ("native",) if group == "index-service" else OPTIONAL_CORE.get(name, ())
        for extra in extras:
            conditions = frozenset({PORTABLE_EXTRA}) if group == "core" else frozenset()
            groups[group].append((name, extra, conditions))
    if any(not roots for roots in groups.values()):
        raise InputError("all three runtime environment roots must be present")
    return groups


def collect_inputs(root: Path) -> tuple[dict[str, list[str]], dict[str, str]]:
    root = root.resolve()
    catalog = _catalog(root)
    documents = {manifest: data for manifest, data in catalog.values()}
    groups, python_ranges = {}, {}
    for group, roots in _roots(root, catalog).items():
        pending = deque((name, extra, inherited, catalog[name][0]) for name, extra, inherited in roots)
        seen = set()
        external = set()
        python = set()
        conditional_python = set()
        while pending:
            name, extra, inherited, manifest = pending.popleft()
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
            if inherited:
                conditional_python.add((name, metadata[0]))
            else:
                python.add(metadata[0])
            for value in _requirements(data["project"], extra, name):
                requirement = Requirement(value)
                dependency = canonicalize_name(requirement.name)
                if requirement.url:
                    raise InputError(f"{name}: direct dependency references are unsupported")
                conditions = inherited
                if requirement.marker:
                    marker = str(requirement.marker)
                    if re.search(r"\b(extra|extras|dependency_groups)\b", marker):
                        raise InputError(f"{name}: context-dependent extra/group marker is unsupported")
                    conditions = conditions | {marker}
                selected = _source_mapping(root, manifest, data, dependency)
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
                requirement.name = dependency
                requirement.marker = None
                rendered = str(requirement)
                if conditions:
                    rendered += "; " + " and ".join(f"({condition})" for condition in sorted(conditions))
                external.add(rendered)
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


def render(root: Path) -> str:
    groups, python_ranges = collect_inputs(root)
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
