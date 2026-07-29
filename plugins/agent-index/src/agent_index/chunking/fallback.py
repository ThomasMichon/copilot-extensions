"""Line-based fallback chunker for unknown file types."""

from __future__ import annotations

from agent_index.chunking.base import Chunk
from agent_index.chunking.code import _split_text


class FallbackChunker:
    """Fixed-size line-based chunking with overlap."""

    def chunk(
        self,
        content: str,
        file_path: str,
        *,
        source: str = "",
        max_tokens: int = 2048,
    ) -> list[Chunk]:
        if not content.strip():
            return []

        splits = _split_text(content, max_tokens)
        return [
            Chunk(
                content=s["content"],
                file_path=file_path,
                chunk_type="text",
                language="text",
                line_start=int(s["offset"]) + 1,
                line_end=int(s["offset"]) + int(s["line_count"]),
                source=source,
            )
            for s in splits
        ]
