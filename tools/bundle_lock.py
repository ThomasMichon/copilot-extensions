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
from packaging.utils import canonicalize_name

from dependency_age import contained_path, manifests
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
        project = data.get("project", {})
        if not isinstance(project, dict):
            raise InputError(f"{manifest.relative_to(root)}: project must be a table")
        name = project.get("name")
        if name is None:
            continue
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise InputError(f"{manifest.relative_to(root)}: invalid distribution name")
        name = canonicalize_name(name)
        if name in result:
            raise InputError(f"ambiguous first-party distribution: {name}")
        if set(project.get("dynamic", [])) & {"dependencies", "optional-dependencies"}:
            raise InputError(f"{name}: dynamic runtime dependencies are unsupported")
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


def _source_mapping(root: Path, manifest: Path, data: dict, name: str) -> None:
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
    target = contained_path(manifest.parent / source["path"] / "pyproject.toml", root)
    if canonicalize_name(_read(target)["project"]["name"]) != name:
        raise InputError(f"{name}: local source distribution identity differs")


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


def dependency_groups(root: Path) -> dict[str, list[str]]:
    root = root.resolve()
    catalog = _catalog(root)
    groups = {}
    for group, roots in _roots(root, catalog).items():
        pending = deque(roots)
        seen = set()
        external = set()
        while pending:
            name, extra, inherited = pending.popleft()
            key = (name, extra, inherited)
            if key in seen:
                continue
            seen.add(key)
            manifest, data = catalog[name]
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
                _source_mapping(root, manifest, data, dependency)
                if dependency in catalog:
                    pending.append((dependency, "", conditions))
                    pending.extend(
                        (dependency, canonicalize_name(selected), conditions)
                        for selected in sorted(requirement.extras)
                    )
                    continue
                sources = data.get("tool", {}).get("uv", {}).get("sources", {})
                if any(canonicalize_name(key) == dependency for key in sources):
                    raise InputError(f"{dependency}: local dependency is absent from canonical inventory")
                requirement.name = dependency
                requirement.marker = None
                rendered = str(requirement)
                if conditions:
                    rendered += "; " + " and ".join(f"({condition})" for condition in sorted(conditions))
                external.add(rendered)
        groups[group] = sorted(external)
    return groups


def render(root: Path) -> str:
    groups = dependency_groups(root)
    lines = [
        "# Generated by tools/bundle_lock.py generate; candidate inputs, not an approved lock.",
        "[project]", 'name = "copilot-extensions-bundle-inputs"', 'version = "0.0.0"',
        'requires-python = ">=3.11"', "", "[tool.uv]", "package = false", "",
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
