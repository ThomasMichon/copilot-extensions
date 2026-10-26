"""Optional, versioned connector output for complete changed-item acquisition."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_index.sources.base import FileEntry


@dataclass
class DeletedFile:
    """An explicit tombstone in the canonical stored source namespace."""

    source: str
    path: str


@dataclass
class SourceDelta:
    """A completed delta, never an implicit complete corpus path listing.

    A connector may implement ``discover_delta(last_commit, cancel_check=None)``
    to return this contract. It owns the base-to-target change coverage and
    target checkpoint. Incomplete pages must return ``complete=False`` or raise,
    not omit unseen items and let the engine infer deletions.
    """

    upserts: list[FileEntry] = field(default_factory=list)
    deletions: list[DeletedFile] = field(default_factory=list)
    commit: str | None = None
    complete: bool = True
    version: int = 1

    def validate(self, family: str) -> None:
        """Reject incompatible/incomplete results before any indexing mutation."""
        if type(self.version) is not int or self.version != 1:
            raise ValueError(f"Unsupported connector delta version: {self.version!r}")
        if self.complete is not True:
            raise ValueError("Connector delta acquisition is incomplete")
        if self.commit is not None and not isinstance(self.commit, str):
            raise ValueError("Connector delta checkpoint must be a string or None")
        if not isinstance(family, str) or not family:
            raise ValueError("Connector delta has no canonical source family")
        upserts: set[tuple[str, str]] = set()
        deleted: set[tuple[str, str]] = set()
        for entries, expected, identities in (
            (self.upserts, FileEntry, upserts),
            (self.deletions, DeletedFile, deleted),
        ):
            if not isinstance(entries, list):
                raise ValueError("Connector delta entries must be lists")
            for entry in entries:
                if not isinstance(entry, expected):
                    raise ValueError("Connector delta contains an invalid entry")
                if not isinstance(entry.source, str) or not (
                    entry.source == family or entry.source.startswith(family + ":")
                ):
                    raise ValueError("Connector delta entry is outside its source family")
                if not isinstance(entry.path, str) or not entry.path:
                    raise ValueError("Connector delta entry has no path")
                identity = (entry.source, entry.path)
                if identity in identities:
                    raise ValueError("Connector delta repeats an item identity")
                identities.add(identity)
        if upserts & deleted:
            raise ValueError("Connector delta both upserts and deletes an item")
