"""Canonical local-source freshness for the repository's cached test venvs."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
from pathlib import Path

from standalone_consumers import standalone_build_resources
if __package__:
    from .native_test_paths import native_test_path
else:
    from native_test_paths import native_test_path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "libs" / "installer-engine"))
sys.path.insert(0, str(_ROOT / "libs" / "versioned-runtime"))
from installer_engine import _package_roots, _source_files  # noqa: E402
from versioned_runtime import fingerprint_source  # noqa: E402

FLEET_TEST_CONSUMERS = frozenset({
    "agent-index", "agent-pull-requests", "agent-dispatch", "agent-ssh",
})


def dependency_fingerprint(
    name: str, plugin: Path, repo: Path, runner_dependencies: tuple[str, ...],
) -> str:
    entries = [plugin] if (plugin / "pyproject.toml").is_file() else []
    resources: tuple[Path, ...] = ()
    if name in FLEET_TEST_CONSUMERS:
        entries.append(repo / "fleet-manager")
        resources = standalone_build_resources("fleet-manager", source_root=repo)
    if name == "agent-dispatch":
        entries.append(repo / "plugins" / "agent-pull-requests")
    roots = set(resources)
    for entry in entries:
        roots.update(_package_roots(entry))
    for root in roots:
        root.resolve(strict=True)
    files = _source_files(tuple(sorted(roots)))
    source_hash = fingerprint_source(files) if files else ""
    digest = hashlib.sha256(b"plugin-test-cache-source-v1\n")
    digest.update(source_hash.encode("ascii"))
    if name == "agent-worktrees":
        digest.update(b"native-bootstrap-uv-cache-v1\n")
    for dependency in runner_dependencies:
        digest.update(f"runner:{dependency}\n".encode("utf-8"))
    return digest.hexdigest()


def ordinary_cache_root(path: Path) -> Path:
    """Refuse linked cache roots before resolving a lease or removing contents."""
    native = native_test_path(path.absolute())
    for candidate in (native, *native.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", 0):
            raise ValueError(f"Test cache directory is not ordinary: {candidate}")
    return path.resolve()


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
