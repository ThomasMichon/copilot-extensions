"""Registrar source identity and location serialization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import repo_config
from .registrar import RegistrarError
from .repository_resources import registered_repository_root, split_repository_resource


@dataclass(frozen=True)
class Pointer:
    """A declaration source, with explicit provenance and canonical lane aliases.

    A repo source uses either a registered ``repository`` name or a legacy root
    ``location``. A directory source uses ``location`` directly. Concrete roots
    for registered sources are resolved per scan, never persisted.
    ``name`` is a label, not a lane identity; only ``aliases`` identify lanes.
    """

    name: str
    location: str = ""
    kind: str = "dir"
    owner: str | None = None
    aliases: tuple[str, ...] = ()
    repository: str | None = None

    def repo_root(self) -> Path:
        if self.repository is not None:
            return registered_repository_root(self.repository)
        return Path(self.location).expanduser()

    def resolved_location(self) -> Path:
        """Select the canonical registrar directory with legacy fallback."""
        base = self.repo_root()
        if self.kind == "repo":
            return repo_config.selected_repo_surface_dir(base, "registrar")
        return base

    def effective_owner(self) -> str:
        """Provenance token: explicit owner, or source-derived identity."""
        if self.owner:
            return self.owner
        stem = self.repository or Path(self.location).expanduser().name or self.name
        return f"repo:{stem}" if self.kind == "repo" else f"pointer:{self.name}"

    def to_dict(self) -> dict[str, str | list[str]]:
        d: dict[str, str | list[str]] = {
            "name": self.name,
            "location": self.location,
            "kind": self.kind,
        }
        if self.owner:
            d["owner"] = self.owner
        if self.aliases:
            d["aliases"] = list(self.aliases)
        if self.repository is not None:
            d.pop("location")
            d["repository"] = self.repository
        return d

    @classmethod
    def from_dict(cls, data: Mapping) -> Pointer:
        if not isinstance(data, Mapping):
            raise RegistrarError(f"pointer: expected a mapping, got {type(data).__name__}")
        name = data.get("name")
        location = data.get("location")
        repository = data.get("repository")
        if not isinstance(name, str) or not name:
            raise RegistrarError("pointer: 'name' is required and must be a non-empty string")
        if repository is not None and (
            not isinstance(repository, str) or not repository
            or location is not None or data.get("kind") != "repo"
        ):
            raise RegistrarError(
                "pointer.repository requires kind 'repo' and a registered name, without location"
            )
        if repository is None and (not isinstance(location, str) or not location):
            raise RegistrarError("pointer: 'location' is required and must be a non-empty string")
        if repository is not None:
            split_repository_resource(f"repo:{repository}:.")
        kind = data.get("kind", "dir")
        if kind not in ("dir", "repo"):
            raise RegistrarError(f"pointer.kind: must be 'dir' or 'repo', got {kind!r}")
        owner = data.get("owner")
        if owner is not None and not isinstance(owner, str):
            raise RegistrarError(f"pointer.owner: expected a string, got {owner!r}")
        raw_aliases = data.get("aliases", [])
        if not isinstance(raw_aliases, list) or not all(
            isinstance(a, str) and a for a in raw_aliases
        ):
            raise RegistrarError("pointer.aliases: expected a list of non-empty strings")
        return cls(
            name=name,
            location=location or "",
            kind=kind,
            owner=owner or None,
            aliases=tuple(raw_aliases),
            repository=repository,
        )
