"""Base classes for data source connectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class FileEntry:
    """A file discovered by a source connector."""

    path: str  # relative path within the source
    content: str
    language: str  # detected language / file type
    source: str  # source identifier
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceConnector(Protocol):
    """Protocol for data source connectors."""

    @property
    def source_name(self) -> str:
        """Unique name for this source (e.g. 'forge', 'forge:owner/repo')."""
        ...

    def discover(
        self,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[FileEntry]:
        """Discover all indexable files from this source.

        Args:
            cancel_check: Optional callable that raises if cancellation
                was requested.  Connectors should call it between
                expensive I/O operations for responsive cancellation.

        Returns:
            List of ``FileEntry`` objects with file content and metadata.
        """
        ...

    def discover_changed(
        self,
        last_commit: str | None,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[FileEntry]:
        """Discover only files changed since the given commit.

        Args:
            last_commit: Git SHA of the last indexed commit, or None for full scan.
            cancel_check: Optional callable that raises if cancellation
                was requested.

        Returns:
            List of changed ``FileEntry`` objects.
        """
        ...

    def list_paths(
        self,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict[str, set[str]]:
        """Return the set of currently-existing paths, grouped by source.

        Cheaper than ``discover()`` since no file content is fetched.
        Used for deletion detection: ``stored_paths - current_paths``
        identifies files that should be removed from the index.

        Args:
            cancel_check: Optional callable that raises if cancellation
                was requested.

        Returns:
            ``{source: {file_paths}}`` — e.g. a Forge connector returns
            ``{"forge:owner/repo": {"file.py", ...},
            "forge:owner/repo:issues": {"issues/1.md", ...}}``.
        """
        ...

    def current_commit(self) -> str | None:
        """Return the current HEAD commit SHA, or None if not git-tracked."""
        ...
