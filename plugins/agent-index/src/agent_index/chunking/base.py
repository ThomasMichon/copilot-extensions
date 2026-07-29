"""Chunk data model and base class for all chunking strategies."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Chunk:
    """A single indexed chunk of content.

    The ``chunk_id`` is a stable composite hash that uniquely identifies
    this chunk across the entire index.  ``content_hash`` is the SHA-256
    of the raw content alone, used for change detection.
    """

    content: str
    file_path: str
    chunk_type: str  # "function", "class", "module", "heading", "yaml-block", "text"
    language: str  # "python", "typescript", "yaml", "markdown", etc.
    line_start: int
    line_end: int
    source: str = ""  # "forge:repo-name", "host:worktree", etc.
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)
    content_hash: str = field(init=False, repr=False)
    chunk_id: str = field(init=False)

    def __post_init__(self) -> None:
        # Normalize content for consistent hashing across sources:
        # CRLF → LF, strip trailing whitespace per line
        normalized = "\n".join(
            line.rstrip() for line in self.content.replace("\r\n", "\n").split("\n")
        )
        h = hashlib.sha256(normalized.encode()).hexdigest()
        object.__setattr__(self, "content_hash", h)
        # Composite identity: source + path + type + location + content
        identity = (
            f"{self.source}:{self.file_path}:{self.chunk_type}"
            f":{self.line_start}:{self.line_end}:{h}"
        )
        object.__setattr__(self, "chunk_id", hashlib.sha256(identity.encode()).hexdigest())


class Chunker(Protocol):
    """Protocol for document chunkers."""

    def chunk(
        self,
        content: str,
        file_path: str,
        *,
        source: str = "",
        max_tokens: int = 2048,
    ) -> list[Chunk]:
        """Split *content* into chunks.

        Args:
            content: Raw file content.
            file_path: Relative path for metadata.
            source: Data source identifier.
            max_tokens: Approximate max tokens per chunk.

        Returns:
            List of ``Chunk`` objects.
        """
        ...
