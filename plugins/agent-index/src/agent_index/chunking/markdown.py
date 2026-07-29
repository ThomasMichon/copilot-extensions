"""Heading-based chunking for Markdown documents.

Splits on ``##`` and ``###`` headings, keeping each section as a chunk
with the heading text as context.
"""

from __future__ import annotations

import re

from agent_index.chunking.base import Chunk
from agent_index.chunking.code import _estimate_tokens, _split_text

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


class MarkdownChunker:
    """Split Markdown by headings."""

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

        sections = _split_by_headings(content)
        chunks: list[Chunk] = []

        for section in sections:
            text = section["text"]
            if not text.strip():
                continue

            if _estimate_tokens(text) <= max_tokens:
                chunks.append(
                    Chunk(
                        content=text,
                        file_path=file_path,
                        chunk_type="heading",
                        language="markdown",
                        line_start=section["line_start"],
                        line_end=section["line_end"],
                        source=source,
                    )
                )
            else:
                # Oversized section — split further
                for sub in _split_text(text, max_tokens):
                    chunks.append(
                        Chunk(
                            content=sub["content"],
                            file_path=file_path,
                            chunk_type="heading",
                            language="markdown",
                            line_start=section["line_start"] + int(sub["offset"]),
                            line_end=(
                                section["line_start"]
                                + int(sub["offset"])
                                + int(sub["line_count"])
                                - 1
                            ),
                            source=source,
                        )
                    )

        return chunks


def _split_by_headings(content: str) -> list[dict]:
    """Split content at heading boundaries (## and ###)."""
    lines = content.split("\n")
    sections: list[dict] = []
    current_lines: list[str] = []
    current_start = 1

    for i, line in enumerate(lines, start=1):
        if _HEADING_RE.match(line) and current_lines:
            # End current section
            sections.append({
                "text": "\n".join(current_lines),
                "line_start": current_start,
                "line_end": i - 1,
            })
            current_lines = [line]
            current_start = i
        else:
            current_lines.append(line)

    # Final section
    if current_lines:
        sections.append({
            "text": "\n".join(current_lines),
            "line_start": current_start,
            "line_end": len(lines),
        })

    return sections
