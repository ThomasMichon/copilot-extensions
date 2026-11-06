"""Canonical local-source freshness for the repository's cached test venvs."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import sys
import tomllib
from pathlib import Path

from standalone_consumers import standalone_build_resources
if __package__:
    from .native_test_paths import native_test_path
else:
    from native_test_paths import native_test_path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
from tools.cache_directory_paths import ordinary_cache_root as ordinary_cache_root  # noqa: E402

sys.path.insert(0, str(_ROOT / "libs" / "installer-engine"))
sys.path.insert(0, str(_ROOT / "libs" / "versioned-runtime"))
from installer_engine import _package_roots, _source_files  # noqa: E402
from versioned_runtime import fingerprint_source  # noqa: E402

FLEET_TEST_CONSUMERS = frozenset({
    "agent-index", "agent-pull-requests", "agent-dispatch", "agent-ssh",
})


def _dependency_sources(name: str, plugin: Path, repo: Path) -> set[Path]:
    entries = [plugin] if (plugin / "pyproject.toml").is_file() else []
    resources: tuple[Path, ...] = ()
    if name in FLEET_TEST_CONSUMERS:
        entries.append(repo / "fleet-manager")
        resources = standalone_build_resources("fleet-manager", source_root=repo)
    if name == "agent-dispatch":
        entries.append(repo / "plugins" / "agent-pull-requests")
    if name == "worktree-manager":
        entries.append(repo / "plugins" / "agent-bridge")
    if name == "payload-invocation":
        entries.append(repo / "libs" / "update-driver")
    roots = set(resources)
    for entry in entries:
        roots.update(_package_roots(entry))
    return roots


def canonical_test_sources(
    name: str, plugin: Path, repo: Path,
) -> tuple[dict[str, Path], tuple[str, ...]]:
    roots = _dependency_sources(name, plugin, repo)
    sources: dict[str, set[Path]] = {}
    for root in roots:
        manifest = root / "pyproject.toml"
        if manifest.is_file():
            package = tomllib.loads(manifest.read_text(encoding="utf-8")).get("project", {}).get("name")
            if not isinstance(package, str):
                continue
            key = re.sub(r"[-_.]+", "-", package).lower()
            sources.setdefault(key, set()).add(root)
    duplicates = {key for key, paths in sources.items() if len(paths) > 1}
    if not duplicates:
        return {}, ()
    canonical: dict[str, Path] = {}
    for manifest in sorted((repo / "libs").glob("*/pyproject.toml")):
        package = tomllib.loads(manifest.read_text(encoding="utf-8")).get("project", {}).get("name")
        if not isinstance(package, str):
            continue
        key = re.sub(r"[-_.]+", "-", package).lower()
        if key in duplicates:
            if key in canonical:
                raise ValueError(f"Test library has multiple canonical sources: {key}")
            _package_roots(manifest.parent)
            canonical[key] = manifest.parent.resolve(strict=True)
    if missing := duplicates - canonical.keys():
        raise ValueError(f"Test dependencies have conflicting non-library sources: {sorted(missing)}")
    for source in canonical.values():
        roots.update(_package_roots(source))
    constraints: set[str] = set()
    for root in roots:
        manifest = root / "pyproject.toml"
        if not manifest.is_file():
            continue
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        project = data.get("project", {})
        uv = data.get("tool", {}).get("uv", {})
        if uv.get("dependency-metadata") or uv.get("extra-build-dependencies"):
            raise ValueError("Canonical test libraries cannot replace dependency metadata")
        sections = [
            project.get("dependencies", []), data.get("build-system", {}).get("requires", []),
            *project.get("optional-dependencies", {}).values(),
            *data.get("dependency-groups", {}).values(),
            *(uv.get(key, []) for key in (
                "dev-dependencies", "constraint-dependencies", "override-dependencies",
                "build-constraint-dependencies",
            )),
        ]
        for section in sections:
            for requirement in section:
                if not isinstance(requirement, str):
                    continue
                match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)(.*)", requirement.strip())
                if match is None or re.sub(r"[-_.]+", "-", match[1]).lower() not in canonical:
                    continue
                if any(character in match[2] for character in "[@;"):
                    raise ValueError("Canonical test libraries require unconditional named dependencies")
                constraints.add(requirement)
    return canonical, tuple(sorted(constraints))


def source_resolution_arguments(name: str, plugin: Path, repo: Path, venv: Path) -> list[str]:
    overrides, constraints = canonical_test_sources(name, plugin, repo)
    if not overrides:
        return []
    override_file = venv / ".source-overrides.txt"
    override_file.write_text("".join(
        f"{package} @ {source.as_uri()}\n" for package, source in sorted(overrides.items())
    ), encoding="utf-8")
    # UV overrides also replace version requirements; retain those bounds separately.
    constraint_file = venv / ".source-constraints.txt"
    constraint_file.write_text("".join(
        requirement + "\n" for requirement in constraints
    ), encoding="utf-8")
    return ["--override", str(override_file), "--constraint", str(constraint_file)]


def dependency_fingerprint(
    name: str, plugin: Path, repo: Path, runner_dependencies: tuple[str, ...],
) -> str:
    roots = _dependency_sources(name, plugin, repo)
    overrides, _ = canonical_test_sources(name, plugin, repo)
    for source in overrides.values():
        roots.update(_package_roots(source))
    for root in roots:
        root.resolve(strict=True)
    files = _source_files(tuple(sorted(roots)))
    source_hash = fingerprint_source(files) if files else ""
    digest = hashlib.sha256(b"plugin-test-cache-source-v1\n")
    digest.update(source_hash.encode("ascii"))
    for package, source in sorted(overrides.items()):
        digest.update(f"canonical-test-source:{package}:{source}\n".encode("utf-8"))
    if name == "agent-worktrees":
        digest.update(b"native-bootstrap-uv-cache-v1\n")
    for dependency in runner_dependencies:
        digest.update(f"runner:{dependency}\n".encode("utf-8"))
    return digest.hexdigest()


def clear_cache_contents(path: Path) -> None:
    """Clear an already-leased ordinary environment without replacing its root."""
    root = native_test_path(ordinary_cache_root(path))
    for child in root.iterdir():
        if child.is_symlink():
            child.unlink()
        elif (
            callable(getattr(child, "is_junction", None)) and child.is_junction()
        ) or (
            os.name == "nt"
            and getattr(child.lstat(), "st_reparse_tag", 0) == stat.IO_REPARSE_TAG_MOUNT_POINT
        ):
            child.rmdir()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
