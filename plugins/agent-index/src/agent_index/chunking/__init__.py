"""Chunking engines — AST-aware, heading-based, and fallback.

Use ``get_chunker()`` to obtain the right strategy for a file type.
"""

from __future__ import annotations

from agent_index.chunking.base import Chunk, Chunker
from agent_index.chunking.code import CodeChunker
from agent_index.chunking.fallback import FallbackChunker
from agent_index.chunking.markdown import MarkdownChunker
from agent_index.chunking.yaml_chunker import YamlChunker

__all__ = [
    "Chunk",
    "Chunker",
    "CodeChunker",
    "FallbackChunker",
    "MarkdownChunker",
    "YamlChunker",
    "get_chunker",
]

_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".sh": "bash",
    ".bash": "bash",
    ".md": "markdown",
    ".yml": "yaml",
    ".yaml": "yaml",
}

_LANGUAGE_CHUNKERS: dict[str, Chunker] = {
    "python": CodeChunker(),
    "javascript": CodeChunker(),
    "typescript": CodeChunker(),
    "bash": CodeChunker(),
    "markdown": MarkdownChunker(),
    "yaml": YamlChunker(),
}

_FALLBACK = FallbackChunker()


def get_chunker(file_path: str) -> tuple[Chunker, str]:
    """Return ``(chunker, language)`` for the given file path.

    Falls back to the line-based chunker for unknown extensions.
    """
    for ext, language in _EXTENSION_MAP.items():
        if file_path.endswith(ext):
            return _LANGUAGE_CHUNKERS.get(language, _FALLBACK), language
    return _FALLBACK, "text"
