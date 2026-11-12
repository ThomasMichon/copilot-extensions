"""Per-pointer discovery evidence, separate from retained process declarations."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from dropin_registry import ScanAuthority

from .registrar import ProfileDeclaration, RegistrarError
from .registrar_configuration import RegistrarIndeterminateError
from .registrar_pointer import Pointer
from .registrar_source_revision import (
    DocumentReads, RevisionState, begin_source_revision, finish_source_revision,
)

ReadStatus = Literal["complete", "missing", "error"]


def observed_repo_directories(root: Path) -> list[Path]:
    from . import repo_config

    directories = repo_config.layered_repo_surface_dirs(root, "registrar", include_unavailable=True)
    canonical = repo_config.canonical_repo_surface_dir(root, "registrar")
    legacy = repo_config.legacy_repo_surface_dir(root, "registrar")
    if not any(path in directories for path in (canonical, legacy)):
        directories.insert(0, canonical)
    return directories


@dataclass(frozen=True)
class RegistrarSourceObservation:
    pointer_name: str
    namespace: str
    kind: str
    aliases: tuple[str, ...]
    root: Path | None
    status: ReadStatus
    declaration_names: tuple[str, ...] = ()
    directories: tuple[Path, ...] = ()
    revision_state: RevisionState = "unavailable"
    revision: str | None = None


@dataclass(frozen=True)
class TrustedDiscovery:
    """Keep the original two-item unpacking/indexing contract."""

    authority: ScanAuthority
    declarations: list[ProfileDeclaration]
    source_observations: tuple[RegistrarSourceObservation, ...] = ()

    def __iter__(self) -> Iterator:
        return iter((self.authority, self.declarations))

    def __getitem__(self, index):
        return (self.authority, self.declarations)[index]

    def __len__(self) -> int:
        return 2


class RegistrarObservationError(RegistrarError):
    def __init__(self, error: Exception, observations: Iterable[RegistrarSourceObservation]):
        super().__init__(str(error))
        self.indeterminate = isinstance(error, (RegistrarIndeterminateError, OSError))
        # A failed aggregate retains old declarations; never pair those with
        # successful partial observations from this attempt.
        self.source_observations = tuple(
            replace(observation, status="error", revision_state="unavailable", revision=None)
            for observation in observations
        )


def observe_trusted(pointers: Iterable[Pointer] | None, *, base: Path | None) -> TrustedDiscovery:
    from . import registrar_discovery as discovery
    from . import repo_config
    from .registrar_document import qualify_supplier_context
    from .repository_resources import RepositoryResourceSnapshot

    if pointers is None:
        authority, pointers = discovery._load_pointers_with_authority(base)
    else:
        authority = ScanAuthority.COMPLETE
    by_name: dict[str, tuple[str, ProfileDeclaration]] = {}
    observations: list[RegistrarSourceObservation] = []
    for pointer in pointers:
        owner = pointer.effective_owner()
        observation = RegistrarSourceObservation(
            pointer.name, owner, pointer.kind, tuple(pointer.aliases), None, "error",
        )
        directory_reads: dict[Path, ReadStatus] = {}
        document_reads: DocumentReads = {}
        try:
            if pointer.kind == "repo":
                snapshot = RepositoryResourceSnapshot(execution=False)
                root = snapshot.root(pointer.repository) if pointer.repository else pointer.repo_root()
                observation = replace(observation, root=root.resolve())
                started = begin_source_revision(root)
                declarations = discovery.read_repo_location_layers(
                    root, owner=owner, directory_reads=directory_reads,
                    document_reads=document_reads,
                )
                if pointer.repository and snapshot.is_read_only(pointer.repository):
                    declarations = [
                        qualify_supplier_context(declaration, pointer.repository, root)
                        for declaration in declarations
                    ]
            else:
                location = pointer.resolved_location()
                root = repo_config.repo_root_from_surface_path(location, "registrar")
                observation = replace(observation, root=(root or location).resolve())
                started = begin_source_revision(root or location)
                declarations = discovery.read_location(
                    location, owner=owner, repo_root=root, directory_reads=directory_reads,
                    document_reads=document_reads,
                )
            for declaration in declarations:
                if declaration.name in by_name:
                    prior_owner = by_name[declaration.name][0]
                    raise RegistrarError(
                        f"duplicate profile name {declaration.name!r}: declared by both "
                        f"{prior_owner!r} and {owner!r} -- names must be unique across the registry"
                    )
                by_name[declaration.name] = (owner, declaration)
        except Exception as error:
            observations.append(replace(observation, directories=tuple(directory_reads)))
            raise RegistrarObservationError(error, observations) from error
        proof = finish_source_revision(
            observation.root, started, directory_reads=directory_reads, document_reads=document_reads,
        )
        observations.append(replace(
            observation,
            status=(
                "error" if "error" in directory_reads.values()
                else "missing" if not directory_reads or "missing" in directory_reads.values()
                else "complete"
            ),
            declaration_names=tuple(sorted(declaration.name for declaration in declarations)),
            directories=tuple(directory_reads),
            revision_state=proof.state, revision=proof.revision,
        ))
    return TrustedDiscovery(
        authority,
        [declaration for _, declaration in sorted(by_name.values(), key=lambda item: item[1].name)],
        tuple(observations),
    )
