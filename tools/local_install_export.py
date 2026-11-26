"""Data-only prefetch for the canonical closed-wheel application export."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import os
from pathlib import Path, PurePosixPath
import tarfile

import prepare_runtime_source as preparation


def _archive_digest(archive: Path, deadline: float) -> bytes:
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        while block := stream.read(1 << 20):
            preparation._remaining(deadline)
            digest.update(block)
    preparation._remaining(deadline)
    return digest.digest()


def _metadata_paths(archive: Path, deadline: float) -> tuple[str, ...]:
    paths = {"pyproject.toml", "bundle/core.json", ".github/plugin/marketplace.json"}
    with tarfile.open(archive, "r:") as stream:
        for member in stream:
            preparation._remaining(deadline)
            if PurePosixPath(member.name).name in {"pyproject.toml", "setup.py"}:
                paths.add(member.name)
    return tuple(sorted(paths))


def _prefetch_paths(
    catalog: Path, project_paths: tuple[str, ...], metadata: tuple[str, ...],
    deadline: float,
) -> tuple[str, ...]:
    import build_python_artifacts as builder
    import uv_editable_ref as uer
    from plugin_runtime_resources import build_owner_roots
    from standalone_consumers import standalone_build_resources
    from standalone_python_artifacts import COMPONENTS

    root = catalog.resolve()
    roots = set(metadata)
    pending = list(project_paths)
    seen: set[str] = set()
    while pending:
        preparation._remaining(deadline)
        relative = pending.pop()
        if relative in seen:
            continue
        seen.add(relative)
        project = Path(os.path.abspath(root / relative))
        if project == root or not project.is_relative_to(root):
            raise ValueError("prefetch input is outside the source")
        roots.add(project.relative_to(root).as_posix())
        resources = build_owner_roots(project, root=root)
        if relative in COMPONENTS.values():
            resources.update(standalone_build_resources(relative, source_root=root))
        pending.extend(path.relative_to(root).as_posix() for path in resources)
        if not (project / "pyproject.toml").is_file():
            continue
        for reader in (
            lambda: uer.find_uv_editable_refs(project, resolve_symlinks=False),
            lambda: builder.find_in_tree_lib_sources(project),
        ):
            for entry in reader():
                target = Path(os.path.abspath(project / entry[1]))
                if target == root or not target.is_relative_to(root):
                    raise ValueError("prefetch dependency is outside the source")
                pending.append(target.relative_to(root).as_posix())
    return tuple(sorted(roots))


def extract_application(
    archive: Path, destination: Path, *, project_paths: tuple[str, ...],
    projects: tuple[str, ...], deadline: float,
    select: Callable[[Path, tuple[str, ...]], tuple[str, ...]],
) -> None:
    from artifact_source_build import PromotionArtifactError
    from build_toolchain_lock import ArtifactBuildError
    from uv_editable_ref import ManifestUnreadable

    identity = archive.stat()
    initial = _archive_digest(archive, deadline)

    def unchanged() -> None:
        current = archive.stat()
        if (
            (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
            != (identity.st_dev, identity.st_ino, identity.st_size, identity.st_mtime_ns)
            or _archive_digest(archive, deadline) != initial
        ):
            raise preparation.SourcePreparationError("Source archive changed during export")

    with preparation._private_work_directory(destination.parent, "export-") as work:
        metadata = _metadata_paths(archive, deadline)
        catalog = work / "catalog"
        catalog.mkdir()
        preparation._extract_archive(archive, catalog, deadline=deadline, include_paths=metadata)
        unchanged()
        hints: tuple[str, ...] | None = None
        compatibility_errors = (
            OSError, ValueError, ArtifactBuildError, PromotionArtifactError, ManifestUnreadable,
        )
        try:
            hints = _prefetch_paths(catalog, project_paths, metadata, deadline)
        except compatibility_errors:
            pass
        preparation._remaining(deadline)
        exported: tuple[str, ...] | None = None
        candidate = work / "candidate"
        if hints is not None:
            candidate.mkdir()
            preparation._extract_archive(archive, candidate, deadline=deadline, include_paths=hints)
            unchanged()
            try:
                exported = select(candidate, projects)
            except compatibility_errors:
                pass
        preparation._remaining(deadline)
        unchanged()
        if exported is None:
            # Closed-wheel Source retains legacy development metadata without building it.
            preparation._extract_archive(archive, destination, deadline=deadline)
            unchanged()
            return
        for relative in exported:
            preparation._remaining(deadline)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            (candidate / relative).rename(target)
        preparation._remaining(deadline)
        if select(destination, projects) != exported:
            raise preparation.SourcePreparationError("Source export closure changed during export")
        unchanged()
