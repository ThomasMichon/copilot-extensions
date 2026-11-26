"""Data-only prefetch for the canonical closed-wheel application export."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path, PurePosixPath
import tarfile
from typing import BinaryIO, cast

import prepare_runtime_source as preparation


def _archive_digest(archive: Path, deadline: float) -> bytes:
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        while block := stream.read(1 << 20):
            preparation._remaining(deadline)
            digest.update(block)
    preparation._remaining(deadline)
    return digest.digest()


class _DigestReader:
    def __init__(self, source: BinaryIO, deadline: float) -> None:
        self.source = source
        self.deadline = deadline
        self.hash = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        preparation._remaining(self.deadline)
        block = self.source.read(size)
        self.hash.update(block)
        return block


@contextmanager
def _archive_pass(
    archive: Path, deadline: float,
) -> Iterator[tuple[tarfile.TarFile, Callable[[], bytes]]]:
    with archive.open("rb") as source:
        reader = _DigestReader(source, deadline)
        # tarfile slices the unread buffer tail for each small member.
        with tarfile.open(
            name=str(archive), fileobj=cast(BinaryIO, reader), mode="r|", bufsize=1 << 16,
        ) as stream:
            yield stream, reader.hash.digest
            # Tar iteration ends before padding or trailing data; bind those bytes too.
            while reader.read(1 << 20):
                pass
        preparation._remaining(deadline)


def _metadata_paths(archive: Path, deadline: float) -> tuple[tuple[str, ...], bytes]:
    paths = {"pyproject.toml", "bundle/core.json", ".github/plugin/marketplace.json"}
    with _archive_pass(archive, deadline) as (stream, digest):
        for member in stream:
            preparation._remaining(deadline)
            if PurePosixPath(member.name).name in {"pyproject.toml", "setup.py"}:
                paths.add(member.name)
    return tuple(sorted(paths)), digest()


def _extract_pass(
    archive: Path, destination: Path, *, deadline: float, include_paths: tuple[str, ...],
) -> bytes:
    with _archive_pass(archive, deadline) as (stream, digest):
        preparation._extract_archive(
            archive, destination, deadline=deadline, include_paths=include_paths,
            archive_stream=stream,
        )
    return digest()


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
    metadata, initial = _metadata_paths(archive, deadline)

    def unchanged(observed: bytes | None = None) -> None:
        current = archive.stat()
        if (
            (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
            != (identity.st_dev, identity.st_ino, identity.st_size, identity.st_mtime_ns)
            or (observed if observed is not None else _archive_digest(archive, deadline)) != initial
        ):
            raise preparation.SourcePreparationError("Source archive changed during export")

    with preparation._private_work_directory(destination.parent, "export-") as work:
        catalog = work / "catalog"
        catalog.mkdir()
        unchanged(_extract_pass(archive, catalog, deadline=deadline, include_paths=metadata))
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
            unchanged(_extract_pass(archive, candidate, deadline=deadline, include_paths=hints))
            try:
                exported = select(candidate, projects)
            except preparation.SourcePreparationError:
                raise
            except (*compatibility_errors, RuntimeError):
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
