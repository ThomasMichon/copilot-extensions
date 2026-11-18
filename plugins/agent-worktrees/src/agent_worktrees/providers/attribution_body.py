"""Preserve an existing PR's source without adopting the updating caller."""

from __future__ import annotations

import base64
import re

from ..codename import is_valid_handle

_SOURCE = "agent-worktrees:source"
_MARKER = re.compile(r"<!--\s*agent-worktrees:source\s+([^\r\n]*?)\s*-->")


def published_marker(body: str) -> str | None:
    """Select one standalone source comment, excluding Markdown examples."""
    from commonmark import Parser

    markers: list[str] = []
    for node, entering in Parser().parse(body).walker():
        if not entering or node.t not in ("html_block", "html_inline"):
            continue
        literal = node.literal or ""
        if _SOURCE not in literal:
            continue
        parent = node.parent
        quoted = False
        while parent is not None:
            quoted |= parent.t == "block_quote"
            parent = parent.parent
        if quoted:
            continue
        marker = literal.strip()
        match = _MARKER.fullmatch(marker)
        if (
            node.t != "html_block" or node.parent is None
            or node.parent.t != "document" or match is None
        ):
            raise ValueError("Source attribution is not a standalone supported comment")
        fields: dict[str, str] = {}
        for token in match[1].split():
            key, separator, value = token.partition("=")
            if not separator or not value or key in fields:
                raise ValueError("Source attribution has malformed or duplicate fields")
            fields[key] = value
        if "codename" in fields:
            if (
                set(fields) - {"codename", "root", "enc"}
                or not is_valid_handle(fields["codename"])
                or "root" in fields
                and not is_valid_handle(fields["root"])
            ):
                raise ValueError("Source attribution has unsupported public fields")
            if "enc" in fields:
                try:
                    raw = base64.b64decode(fields["enc"], validate=True)
                except ValueError:
                    raise ValueError("Source attribution envelope is malformed") from None
                if (
                    len(fields["enc"]) > 16384
                    or len(raw) < 32
                    or not raw.startswith(b"AWI1")
                    or base64.b64encode(raw).decode("ascii") != fields["enc"]
                ):
                    raise ValueError("Source attribution envelope is malformed")
        elif "worktree" in fields:
            if set(fields) - {"worktree", "machine", "session", "head"}:
                raise ValueError("Source attribution has unsupported internal fields")
        else:
            raise ValueError("Source attribution provenance is unsupported")
        markers.append(marker)
    if len(markers) > 1:
        raise ValueError("Source attribution is ambiguous; retain one original marker")
    return markers[0] if markers else None


def preserve_description(current: str, requested: str) -> str:
    """Keep authored bytes and original source; unavailable recovery refuses."""
    original = published_marker(current)
    if original is None:
        raise ValueError(
            "Original source attribution is unavailable; reconcile through the PR's "
            "source owner before editing its description. Title-only updates remain available"
        )
    supplied = published_marker(requested)
    if supplied is not None:
        if supplied != original:
            raise ValueError(
                "Requested description conflicts with the original source attribution"
            )
        return requested
    newline = "\r\n" if requested.endswith("\r\n") else "\n"
    separator = (
        ""
        if not requested or requested.endswith(newline * 2)
        else (newline if requested.endswith(newline) else newline * 2)
    )
    composed = requested + separator + original + newline
    if published_marker(composed) != original:
        raise ValueError(
            "Description has an open literal boundary; source attribution cannot be retained"
        )
    return composed
