"""Preserve an existing PR's source without adopting the updating caller."""

from __future__ import annotations

import base64
from collections.abc import Iterator
import re

from ..codename import is_valid_handle

_SOURCE = "agent-worktrees:source"
_MARKER = re.compile(r"<!--\s*agent-worktrees:source\s+([^\r\n]*?)\s*-->")


def _html_literals(body: str) -> Iterator[tuple[str, bool]]:
    from marko.block import HTMLBlock, Quote
    from marko.element import Element
    from marko.inline import InlineHTML
    from marko.parser import Parser

    document = Parser().parse(body)
    pending: list[tuple[Element, bool]] = [
        (node, True) for node in reversed(document.children)
    ]
    while pending:
        node, standalone = pending.pop()
        if isinstance(node, Quote):
            continue
        if isinstance(node, HTMLBlock):
            yield node.body, standalone
        elif isinstance(node, InlineHTML):
            if not isinstance(node.children, str):
                raise ValueError("Source attribution HTML literal is unavailable")
            yield node.children, False
        else:
            children: object = getattr(node, "children", ())
            if isinstance(children, (list, tuple)):
                pending.extend(
                    (child, False) for child in reversed(children)
                    if isinstance(child, Element)
                )


def published_marker(body: str) -> str | None:
    """Select one standalone source comment, excluding Markdown examples."""
    markers: list[str] = []
    for literal, standalone in _html_literals(body):
        if _SOURCE not in literal:
            continue
        marker = literal.strip()
        match = _MARKER.fullmatch(marker)
        if not standalone or match is None:
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
