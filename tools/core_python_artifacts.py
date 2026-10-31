"""Canonical first-party core composition for the existing promotion channel."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from artifact_source_build import PromotionArtifactError
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version
from python_project_inventory import contained_path, manifests, runtime_roots
from standalone_python_artifacts import COMPONENTS, validate_build
from standalone_python_artifacts import source_closure as standalone_closure

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

SELECTION_PATH = Path("bundle") / "core.json"


@dataclass(frozen=True)
class CoreSources:
    version: str
    projects: tuple[tuple[str, Path], ...]
    resources: tuple[Path, ...]
    selection: dict


def enabled(root: Path) -> bool:
    path = root / SELECTION_PATH
    if not path.exists() and not path.is_symlink():
        return False
    try:
        value = json.loads(contained_path(path, root.resolve()).read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict) or type(value.get("version")) is not int or value != {
                "schema": "copilot-extensions.core-composition", "version": 1,
            }
        ):
            raise ValueError("invalid selection")
        return True
    except (OSError, ValueError) as exc:
        raise PromotionArtifactError("invalid core composition selection") from exc


def release_version(root: Path) -> str:
    try:
        root = root.resolve()
        path = contained_path(root / ".github" / "plugin" / "marketplace.json", root)
        version = json.loads(path.read_text(encoding="utf-8"))["metadata"]["version"]
        if not isinstance(version, str) or not version or str(Version(version)) != version.replace("-", "."):
            raise ValueError("invalid version")
        return version
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise PromotionArtifactError("invalid core marketplace version") from exc


def _read(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _runtime_metadata(project: dict) -> tuple:
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list) or set(dynamic) & {
        "dependencies", "optional-dependencies", "requires-python",
    }:
        raise PromotionArtifactError("core requires static runtime metadata")
    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, dict):
        raise PromotionArtifactError("core optional dependencies must be a table")
    sections = {"": project.get("dependencies", [])}
    for extra, values in optional.items():
        name = canonicalize_name(extra, validate=True)
        if name in sections:
            raise PromotionArtifactError("ambiguous core optional dependency")
        sections[name] = values
    result = []
    for name, requirements in sections.items():
        if not isinstance(requirements, list) or any(not isinstance(value, str) for value in requirements):
            raise PromotionArtifactError("core runtime dependencies must be strings")
        result.append((name, tuple(sorted(str(Requirement(value)) for value in requirements))))
    return (
        str(Version(project["version"])), str(SpecifierSet(project.get("requires-python", ""))),
        tuple(sorted(result)),
    )


def source_closure(root: Path, builder) -> CoreSources:
    """Keep conditional edges as data; never evaluate them on the build host."""
    root = root.resolve()
    try:
        catalog = {}
        for manifest in manifests(root):
            manifest = contained_path(manifest, root)
            data = _read(manifest)
            project = data["project"]
            name = canonicalize_name(project["name"], validate=True)
            if name in catalog:
                raise PromotionArtifactError(f"ambiguous first-party distribution: {name}")
            _runtime_metadata(project)
            catalog[name] = (manifest.parent, data)
        groups = runtime_roots(root, catalog)
        roots = [
            {"name": name, "extra": extra, "condition": " and ".join(sorted(conditions))}
            for name, extra, conditions in groups["core"]
        ]
        if not roots:
            raise PromotionArtifactError("core composition has no runtime roots")
        pending = [(entry["name"], entry["extra"]) for entry in roots]
        seen, selected, edges = set(), set(), []
        while pending:
            name, extra = pending.pop()
            if (name, extra) in seen:
                continue
            seen.add((name, extra))
            selected.add(name)
            directory, data = catalog[name]
            project = data["project"]
            extras = project.get("optional-dependencies", {})
            matches = [values for key, values in extras.items() if canonicalize_name(key) == extra]
            if extra and len(matches) != 1:
                raise PromotionArtifactError(f"{name}: unknown or ambiguous core extra {extra}")
            requirements = matches[0] if extra else project.get("dependencies", [])
            for text in requirements:
                requirement = Requirement(text)
                dependency = canonicalize_name(requirement.name)
                edges.append({"name": name, "extra": extra, "requirement": str(requirement)})
                sources = data.get("tool", {}).get("uv", {}).get("sources", {})
                mappings = [value for key, value in sources.items() if canonicalize_name(key) == dependency]
                if dependency not in catalog:
                    from seasoned_uv import _bundled_dbus_next
                    for mapping in mappings:
                        for entry in (mapping if isinstance(mapping, list) else [mapping]):
                            if isinstance(entry, dict) and "path" in entry:
                                path = entry["path"]
                                if not isinstance(path, str) or not _bundled_dbus_next(
                                    name, dependency, contained_path(directory / path, root),
                                ):
                                    raise PromotionArtifactError(f"{dependency}: local dependency has no canonical source")
                    continue
                if requirement.url or not requirement.specifier.contains(
                    Version(catalog[dependency][1]["project"]["version"]), prereleases=True,
                ):
                    raise PromotionArtifactError(f"{name}: incompatible first-party dependency {text}")
                if mappings:
                    from standalone_consumers import local_dependency_source
                    mapped = local_dependency_source(directory, dependency, source_root=root)
                    mapped_project = _read(mapped / "pyproject.toml")["project"]
                    canonical = catalog[dependency][1]["project"]
                    if _runtime_metadata(mapped_project) != _runtime_metadata(canonical):
                        raise PromotionArtifactError(f"{dependency}: local and canonical runtime metadata differ")
                pending.extend(
                    (dependency, value)
                    for value in ("", *sorted(canonicalize_name(value) for value in requirement.extras))
                )
        if selected & {name for group in ("index-service", "index-engine") for name, _extra, _condition in groups[group]}:
            raise PromotionArtifactError("core dependency crosses an optional environment boundary")
        projects, resources, records = [], set(), []
        reverse = {value: key for key, value in COMPONENTS.items()}
        root_names = {entry["name"] for entry in roots}
        for name in sorted(selected):
            directory, _data = catalog[name]
            relative = directory.relative_to(root).as_posix()
            role = "vendored-lib"
            if name in root_names:
                role = "standalone" if relative in reverse else "plugin"
            if relative in reverse:
                _source, _dependencies, inputs = standalone_closure(reverse[relative], root, builder)
                validate_build(directory, component=reverse[relative], root=root)
                resources.update(inputs)
            else:
                from plugin_runtime_resources import build_inputs
                build_inputs(directory, root=root)
            if builder.uer._find_symlink(directory, skip_test_runs=False) is not None:
                raise PromotionArtifactError("core source tree contains a symlink")
            projects.append((role, directory))
            records.append({"name": name, "source": relative, "role": role})
        selection = {
            "roots": roots, "projects": records,
            "edges": sorted(edges, key=lambda edge: (edge["name"], edge["extra"], edge["requirement"])),
        }
        return CoreSources(
            release_version(root), tuple(projects), tuple(sorted(resources)), selection,
        )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PromotionArtifactError):
            raise
        raise PromotionArtifactError(f"invalid core composition: {exc}") from exc


def payload_hash(builder, closure: CoreSources, selection: dict | None = None) -> str:
    from build_toolchain_lock import _hash_fields
    return "sha256:" + _hash_fields(
        builder.compute_payload_hash([*(path for _role, path in closure.projects), *closure.resources]),
        closure.version,
        json.dumps(closure.selection if selection is None else selection, sort_keys=True, separators=(",", ":")),
    )


def build_core_artifacts(*, out_dir: Path, python=None, toolchain=None, builder=None) -> dict:
    if builder is None:
        import build_python_artifacts as builder
    try:
        closure = source_closure(builder.REPO, builder)
    except PromotionArtifactError as exc:
        raise builder.ArtifactBuildError(str(exc)) from exc
    return builder.build_project_artifacts(
        identity={"composition": "core"}, projects=list(closure.projects),
        raw_version=closure.version, schema_version=5, resources=closure.resources,
        selection=closure.selection, payload_hash=payload_hash(builder, closure),
        out_dir=out_dir, python=python, toolchain=toolchain,
    )
