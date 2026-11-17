"""Derive execution admission from a Dev inventory and composed source bytes."""
from __future__ import annotations

import hashlib
from pathlib import Path

from promotion_unit_contract import (
    CANDIDATE_PROFILE, UnitEvidenceError, admit_manifest, manifest_digest,
    relative_file, source_sha, verify_sources,
)


def candidate_manifest(
    template: dict, *, dev_sha: str, candidate_tree: str,
    development_sources: dict[str, bytes], candidate_sources: dict[str, bytes],
    origins: dict[str, str],
) -> dict:
    """Keep positive selection fixed; bind the actual composed execution closure."""
    template = admit_manifest(template)
    if template["profile"] == CANDIDATE_PROFILE:
        raise UnitEvidenceError("candidate admission requires an original Dev manifest")
    source_sha(dev_sha)
    source_sha(candidate_tree)
    if set(development_sources) != set(template["sources"]):
        raise UnitEvidenceError("development closure differs from the admitted template")
    for name, raw in development_sources.items():
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != template["sources"][name]:
            raise UnitEvidenceError(f"development source changed without admission: {name}")
    if not set(template["sources"]) <= set(candidate_sources) or set(origins) != set(candidate_sources):
        raise UnitEvidenceError("candidate closure is missing an admitted source or origin")
    for name, origin in origins.items():
        relative_file(name)
        relative_file(origin)
        if origin not in template["sources"] or (name in template["sources"] and origin != name):
            raise UnitEvidenceError("candidate closure has an unadmitted origin")
        if name != origin and candidate_sources[name] != candidate_sources[origin]:
            raise UnitEvidenceError("vendored unit input differs from its composed canonical origin")
    sources = {}
    for name, raw in sorted(candidate_sources.items()):
        if not isinstance(raw, bytes):
            raise UnitEvidenceError("candidate source content must be raw bytes")
        sources[name] = hashlib.sha256(raw).hexdigest()
    return admit_manifest({
        **template, "profile": CANDIDATE_PROFILE, "sources": sources,
        "coverage_targets": sorted(
            name for name, origin in origins.items() if origin in template["coverage_targets"]
        ),
        "composition": {
            "dev_sha": dev_sha, "candidate_tree": candidate_tree,
            "source_manifest_sha256": manifest_digest(template),
            "development_sources": template["sources"], "origins": dict(sorted(origins.items())),
            "required_coverage_origins": template["coverage_targets"],
        },
    })


def _bytes(root: Path, names) -> dict[str, bytes]:
    result = {}
    for name in names:
        path = root / relative_file(name)
        current = root
        for part in Path(name).parts:
            current /= part
            if current.is_symlink():
                raise UnitEvidenceError(f"indirect candidate source: {name}")
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise UnitEvidenceError(f"missing candidate source: {name}")
        result[name] = path.read_bytes()
    return result


def derive_manifest(
    template: dict, *, development_root: Path, candidate_root: Path,
    dev_sha: str, candidate_tree: str,
) -> tuple[dict, dict[str, bytes], dict[str, bytes]]:
    """Admit exact copies only through the composer's declared uv vendor route."""
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    verify_sources(development_root.resolve(), template["sources"])
    development = _bytes(development_root, template["sources"])
    candidate = _bytes(candidate_root, template["sources"])
    origins = {name: name for name in candidate}
    projects = list(candidate_root.glob("plugins/*/pyproject.toml"))
    projects += list(candidate_root.glob("*/pyproject.toml"))
    projects += list(candidate_root.glob("services/*/pyproject.toml"))
    for project in projects:
        original = development_root / project.relative_to(candidate_root)
        if not original.is_file() or original.is_symlink() or project.is_symlink():
            continue
        before = tomllib.loads(original.read_text(encoding="utf-8")).get("tool", {}).get("uv", {}).get("sources", {})
        after = tomllib.loads(project.read_text(encoding="utf-8")).get("tool", {}).get("uv", {}).get("sources", {})
        for dependency, spec in before.items():
            composed = after.get(dependency)
            if not isinstance(spec, dict) or not isinstance(composed, dict):
                continue
            src, dst = spec.get("path"), composed.get("path")
            if not isinstance(src, str) or not isinstance(dst, str) or not dst.startswith("libs/"):
                continue
            source = (original.parent / src).resolve()
            destination = (project.parent / dst).resolve()
            if not source.is_relative_to(development_root.resolve()) or not destination.is_relative_to(candidate_root.resolve()):
                raise UnitEvidenceError("vendor admission escapes the composition roots")
            prefix = source.relative_to(development_root.resolve()).as_posix() + "/"
            for name in development:
                if name.startswith(prefix):
                    copy = (destination / name.removeprefix(prefix)).relative_to(candidate_root.resolve()).as_posix()
                    if copy != name:
                        candidate.update(_bytes(candidate_root, [copy]))
                        origins[copy] = name
    admitted = candidate_manifest(
        template, dev_sha=dev_sha, candidate_tree=candidate_tree,
        development_sources=development, candidate_sources=candidate, origins=origins,
    )
    return admitted, development, candidate
