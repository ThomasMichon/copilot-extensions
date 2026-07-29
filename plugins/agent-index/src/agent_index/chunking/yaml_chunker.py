"""Block-based chunking for YAML files.

Splits on top-level keys, treating each as a separate chunk.
Designed for structured YAML configuration files.
"""

from __future__ import annotations

import re

from agent_index.chunking.base import Chunk
from agent_index.chunking.code import _estimate_tokens, _split_text

# Top-level YAML key: non-indented line ending with ':'
_TOP_LEVEL_KEY_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_-]*):\s*(.*)$")


class YamlChunker:
    """Split YAML by top-level keys."""

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

        blocks = _split_by_top_level_keys(content)
        chunks: list[Chunk] = []

        for block in blocks:
            text = block["text"]
            if not text.strip():
                continue

            if _estimate_tokens(text) <= max_tokens:
                chunks.append(
                    Chunk(
                        content=text,
                        file_path=file_path,
                        chunk_type="yaml-block",
                        language="yaml",
                        line_start=block["line_start"],
                        line_end=block["line_end"],
                        source=source,
                    )
                )
            else:
                for sub in _split_text(text, max_tokens):
                    chunks.append(
                        Chunk(
                            content=sub["content"],
                            file_path=file_path,
                            chunk_type="yaml-block",
                            language="yaml",
                            line_start=block["line_start"] + int(sub["offset"]),
                            line_end=(
                                block["line_start"]
                                + int(sub["offset"])
                                + int(sub["line_count"])
                                - 1
                            ),
                            source=source,
                        )
                    )

        return chunks


def _split_by_top_level_keys(content: str) -> list[dict]:
    """Split YAML content at top-level key boundaries."""
    lines = content.split("\n")
    blocks: list[dict] = []
    current_lines: list[str] = []
    current_start = 1

    for i, line in enumerate(lines, start=1):
        # A top-level key is a non-indented, non-comment line with ':'
        if _TOP_LEVEL_KEY_RE.match(line) and not line.startswith(" ") and current_lines:
            blocks.append({
                "text": "\n".join(current_lines),
                "line_start": current_start,
                "line_end": i - 1,
            })
            current_lines = [line]
            current_start = i
        else:
            current_lines.append(line)

    if current_lines:
        blocks.append({
            "text": "\n".join(current_lines),
            "line_start": current_start,
            "line_end": len(lines),
        })

    return blocks
