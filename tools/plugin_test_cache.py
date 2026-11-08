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
from typing import Any

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
    "agent-index", "agent-pull-requests", "agent-dispatch", "agent-ssh", "agent-worktrees",
})


def has_dev_extra(project: Path) -> bool:
    """Read the development extra without executing package metadata."""
    try:
        data = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return "dev" in data.get("project", {}).get("optional-dependencies", {})


def declared_test_requirements(plugin: Path, additional: tuple[str, ...]) -> tuple[str, ...]:
    """Select test-only declarations, never application runtime/core dependencies."""
    manifest = plugin / "pyproject.toml"
    declared = []
    if manifest.is_file():
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        project = data.get("project", {})
        if not isinstance(project, dict):
            raise ValueError("Test tooling requires a project table")
        extras = project.get("optional-dependencies", {})
        groups = data.get("dependency-groups", {})
        if not isinstance(extras, dict) or not isinstance(groups, dict):
            raise ValueError("Test tooling requires optional-dependencies and dependency-groups tables")
        declared = extras.get("dev", groups.get("dev", []))
        if not isinstance(declared, list) or any(not isinstance(value, str) for value in declared):
            raise ValueError("Test tooling requires a flat declared dev requirement list")
    return tuple(dict.fromkeys(("pytest", *declared, *additional)))


def _dependency_entries(name: str, plugin: Path, repo: Path) -> list[tuple[Path, tuple[str, ...]]]:
    entries: list[tuple[Path, tuple[str, ...]]] = []
    manifest = plugin / "pyproject.toml"
    if manifest.is_file():
        extras = tomllib.loads(manifest.read_text(encoding="utf-8")).get(
            "project", {},
        ).get("optional-dependencies", {})
        entries.append((plugin, ("dev",) if "dev" in extras else ()))
    if name in FLEET_TEST_CONSUMERS:
        extra = {
            "agent-index": ("auth",), "agent-pull-requests": ("service-gateway",),
            "agent-dispatch": ("service-gateway",), "agent-ssh": (), "agent-worktrees": ("auth",),
        }[name]
        entries.append((repo / "fleet-manager", extra))
    if name == "agent-dispatch":
        entries.append((repo / "plugins" / "agent-pull-requests", ("fleet",)))
    if name == "worktree-manager":
        entries.append((repo / "plugins" / "agent-bridge", ()))
    if name == "payload-invocation":
        entries.append((repo / "libs" / "update-driver", ()))
    return entries


def retained_build_resources(name: str, repo: Path) -> tuple[Path, ...]:
    """Share standalone build inputs between preparation and cache freshness."""
    return (
        standalone_build_resources("fleet-manager", source_root=repo)
        if name in FLEET_TEST_CONSUMERS else ()
    )


def _dependency_sources(name: str, plugin: Path, repo: Path) -> set[Path]:
    roots = set(retained_build_resources(name, repo))
    for entry, _ in _dependency_entries(name, plugin, repo):
        roots.update(_package_roots(entry))
    return roots


def _bind_extra(marker: str, extra: str) -> str:
    tokens = re.compile(r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|(?P<extra>\bextra\b)""")
    matches = list(tokens.finditer(marker))
    replacements = {}
    for index, token in enumerate(matches):
        if token.lastgroup != "extra":
            continue
        replacements[token.span()] = repr(extra)
        literal = False
        for offset in (-1, 1):
            neighbor_index = index + offset
            if not 0 <= neighbor_index < len(matches):
                continue
            neighbor = matches[neighbor_index]
            left, right = (neighbor, token) if offset < 0 else (token, neighbor)
            if not re.fullmatch(r"\s*(?:===|==|!=|~=|<=|>=|<|>|in|not\s+in)\s*",
                                marker[left.end():right.start()]):
                continue
            if neighbor.lastgroup != "extra":
                value = re.sub(r"[-_.]+", "-", neighbor[0][1:-1]).lower()
                replacements[neighbor.span()] = repr(value)
                literal = True
        if not literal:
            raise ValueError("Extra markers require a named literal comparison")
    return tokens.sub(lambda token: replacements.get(token.span(), token[0]), marker)


def _active_library_constraints(
    entries: list[tuple[Path, tuple[str, ...]]], packages: dict[str, Path],
    canonical: dict[str, Path],
) -> tuple[str, ...]:
    pending: list[tuple[Path, str, frozenset[str]]] = [
        (root, extra, frozenset()) for root, extras in entries for extra in ("", *extras)
    ]
    seen: set[tuple[Path, str, frozenset[str]]] = set()
    constraints: set[str] = set()
    metadata: dict[Path, dict[str, Any]] = {}
    while pending:
        root, extra, inherited = pending.pop()
        context = root, extra, inherited
        if context in seen:
            continue
        seen.add(context)
        if root not in metadata:
            metadata[root] = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        data = metadata[root]
        uv = data.get("tool", {}).get("uv", {})
        if uv.get("dependency-metadata") or uv.get("extra-build-dependencies"):
            raise ValueError("Canonical test libraries cannot replace dependency metadata")
        project = data.get("project", {})
        optional = {
            re.sub(r"[-_.]+", "-", key).lower(): value
            for key, value in project.get("optional-dependencies", {}).items()
        }
        requirements = [
            *project.get("dependencies", []), *(optional.get(extra, []) if extra else []),
        ]
        for requirement in requirements:
            match = re.fullmatch(
                r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*"
                r"(?:\[([A-Za-z0-9._,\s-]+)\])?\s*([^;]*)(?:;(.*))?", requirement,
            )
            if match is None:
                raise ValueError("Test dependencies require named requirements")
            package = re.sub(r"[-_.]+", "-", match[1]).lower()
            specification = match[3].strip()
            # Bind the declaring extra, but let UV evaluate markers for its selected target.
            marker = _bind_extra((match[4] or "").strip(), extra)
            predicates = inherited | ({marker} if marker else set())
            if package in canonical:
                if "@" in specification:
                    raise ValueError("Canonical test libraries cannot replace direct source requirements")
                constraint = package + specification
                if predicates:
                    constraint += "; " + " and ".join(f"({value})" for value in sorted(predicates))
                constraints.add(constraint)
            if package in packages:
                extras = tuple(
                    re.sub(r"[-_.]+", "-", value.strip()).lower()
                    for value in (match[2] or "").split(",") if value.strip()
                )
                pending.extend(
                    (packages[package], selected, frozenset(predicates)) for selected in ("", *extras)
                )
    return tuple(sorted(constraints))


def _require_unconditional_sources(roots: set[Path]) -> None:
    for root in roots:
        manifest = root / "pyproject.toml"
        if not manifest.is_file():
            continue
        mappings = tomllib.loads(manifest.read_text(encoding="utf-8")).get(
            "tool", {},
        ).get("uv", {}).get("sources", {})
        for entries in mappings.values():
            for entry in entries if isinstance(entries, list) else [entries]:
                if isinstance(entry, dict) and {"marker", "extra", "group"} & entry.keys():
                    raise ValueError("Canonical test composition requires unconditional source mappings")


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
    packages = {key: canonical.get(key, next(iter(paths))) for key, paths in sources.items()}
    for source in canonical.values():
        for root in _package_roots(source):
            roots.add(root)
            manifest = root / "pyproject.toml"
            package = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]["name"]
            packages.setdefault(re.sub(r"[-_.]+", "-", package).lower(), root)
    _require_unconditional_sources(roots)
    return canonical, _active_library_constraints(
        _dependency_entries(name, plugin, repo), packages, canonical,
    )


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
