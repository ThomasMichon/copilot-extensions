"""Tree-sitter AST chunking for code files.

Extracts functions, classes, and top-level modules from source code.
Supports Python, TypeScript, JavaScript, and Bash via tree-sitter.
"""

from __future__ import annotations

import logging

from agent_index.chunking.base import Chunk

logger = logging.getLogger(__name__)

# Lazy-loaded language modules — maps extension/language to tree-sitter module
_LANG_MODULES: dict[str, tuple[str, str]] = {
    "python": ("tree_sitter_python", "python"),
    "javascript": ("tree_sitter_javascript", "javascript"),
    "typescript": ("tree_sitter_typescript", "typescript"),
    "bash": ("tree_sitter_bash", "bash"),
}

# AST node types that constitute meaningful chunk boundaries
_CHUNK_NODE_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition", "decorated_definition"},
    "javascript": {
        "function_declaration",
        "class_declaration",
        "arrow_function",
        "method_definition",
        "export_statement",
    },
    "typescript": {
        "function_declaration",
        "class_declaration",
        "arrow_function",
        "method_definition",
        "export_statement",
        "interface_declaration",
        "type_alias_declaration",
    },
    "bash": {"function_definition"},
}


class CodeChunker:
    """AST-aware code chunker using tree-sitter."""

    def chunk(
        self,
        content: str,
        file_path: str,
        *,
        source: str = "",
        max_tokens: int = 2048,
    ) -> list[Chunk]:
        language = _detect_language(file_path)
        if language is None or language not in _LANG_MODULES:
            return _fallback_chunk(content, file_path, language or "unknown", source, max_tokens)

        try:
            tree = _parse(content, language)
        except Exception:
            logger.warning("Tree-sitter parse failed for %s, using fallback", file_path)
            return _fallback_chunk(content, file_path, language, source, max_tokens)

        chunks: list[Chunk] = []
        lines = content.split("\n")
        node_types = _CHUNK_NODE_TYPES.get(language, set())

        _extract_chunks(
            tree.root_node,
            lines,
            file_path,
            language,
            source,
            node_types,
            max_tokens,
            chunks,
        )

        # If we got no chunks (e.g. a flat script with no functions), chunk the whole file
        if not chunks:
            return _fallback_chunk(content, file_path, language, source, max_tokens)

        return chunks


def _detect_language(file_path: str) -> str | None:
    """Infer language from file extension."""
    ext_map: dict[str, str] = {
        ".py": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".sh": "bash",
        ".bash": "bash",
    }
    for ext, lang in ext_map.items():
        if file_path.endswith(ext):
            return lang
    return None


def _parse(content: str, language: str):
    """Parse source code with tree-sitter."""
    from tree_sitter import Language, Parser

    mod_name, _lang_name = _LANG_MODULES[language]
    mod = __import__(mod_name)
    ts_language = Language(mod.language())

    parser = Parser(ts_language)
    return parser.parse(content.encode())


def _extract_chunks(
    node,
    lines: list[str],
    file_path: str,
    language: str,
    source: str,
    node_types: set[str],
    max_tokens: int,
    chunks: list[Chunk],
) -> None:
    """Recursively extract chunks from AST nodes."""
    if node.type in node_types:
        start_line = node.start_point[0]
        end_line = node.end_point[0]
        text = "\n".join(lines[start_line : end_line + 1])

        # Determine chunk_type
        chunk_type = _classify_node(node.type)

        if _estimate_tokens(text) <= max_tokens:
            chunks.append(
                Chunk(
                    content=text,
                    file_path=file_path,
                    chunk_type=chunk_type,
                    language=language,
                    line_start=start_line + 1,  # 1-indexed
                    line_end=end_line + 1,
                    source=source,
                )
            )
        else:
            # Large node — split into sub-chunks via children or fallback
            _split_large_node(
                node, lines, file_path, language, source, node_types, max_tokens, chunks
            )
        return

    # Recurse into children
    for child in node.children:
        _extract_chunks(child, lines, file_path, language, source, node_types, max_tokens, chunks)


def _split_large_node(
    node,
    lines: list[str],
    file_path: str,
    language: str,
    source: str,
    node_types: set[str],
    max_tokens: int,
    chunks: list[Chunk],
) -> None:
    """Split an oversized AST node into smaller chunks."""
    # Try extracting child definitions first
    child_chunks: list[Chunk] = []
    for child in node.children:
        _extract_chunks(
            child, lines, file_path, language, source, node_types, max_tokens, child_chunks
        )

    if child_chunks:
        chunks.extend(child_chunks)
    else:
        # No child definitions — fall back to line-based splitting
        start_line = node.start_point[0]
        end_line = node.end_point[0]
        text = "\n".join(lines[start_line : end_line + 1])
        for sub in _split_text(text, max_tokens):
            chunks.append(
                Chunk(
                    content=sub["content"],
                    file_path=file_path,
                    chunk_type=_classify_node(node.type),
                    language=language,
                    line_start=start_line + sub["offset"] + 1,
                    line_end=start_line + sub["offset"] + sub["line_count"],
                    source=source,
                )
            )


def _classify_node(node_type: str) -> str:
    """Map tree-sitter node types to chunk_type values."""
    if "class" in node_type or "interface" in node_type:
        return "class"
    if "function" in node_type or "method" in node_type or "arrow" in node_type:
        return "function"
    if "type" in node_type:
        return "type"
    if "export" in node_type:
        return "module"
    return "module"


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for code."""
    return len(text) // 4


def _split_text(
    text: str,
    max_tokens: int,
    overlap_tokens: int = 128,
) -> list[dict[str, int | str]]:
    """Split text into overlapping line-based windows."""
    lines = text.split("\n")
    max_chars = max_tokens * 4
    overlap_chars = overlap_tokens * 4
    result: list[dict[str, int | str]] = []

    current_lines: list[str] = []
    current_chars = 0
    offset = 0

    for i, line in enumerate(lines):
        current_lines.append(line)
        current_chars += len(line) + 1

        if current_chars >= max_chars:
            result.append({
                "content": "\n".join(current_lines),
                "offset": offset,
                "line_count": len(current_lines),
            })
            # Start new window with overlap
            overlap_lines: list[str] = []
            overlap_size = 0
            for back_line in reversed(current_lines):
                if overlap_size + len(back_line) > overlap_chars:
                    break
                overlap_lines.insert(0, back_line)
                overlap_size += len(back_line) + 1

            offset = i - len(overlap_lines) + 1
            current_lines = list(overlap_lines)
            current_chars = overlap_size

    if current_lines:
        result.append({
            "content": "\n".join(current_lines),
            "offset": offset,
            "line_count": len(current_lines),
        })

    return result


def _fallback_chunk(
    content: str,
    file_path: str,
    language: str,
    source: str,
    max_tokens: int,
) -> list[Chunk]:
    """Line-based fallback when AST parsing is unavailable."""
    if not content.strip():
        return []

    splits = _split_text(content, max_tokens)
    return [
        Chunk(
            content=s["content"],
            file_path=file_path,
            chunk_type="module",
            language=language,
            line_start=int(s["offset"]) + 1,
            line_end=int(s["offset"]) + int(s["line_count"]),
            source=source,
        )
        for s in splits
    ]
