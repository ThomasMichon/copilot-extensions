"""Pure value operations for registrar recipe expansion."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .registrar import RegistrarError

_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Merge mappings recursively; override replaces all non-mapping values."""
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _fill(value: str, params: Mapping[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(params[key]) if key in params else match.group(0)

    return _PLACEHOLDER_RE.sub(replace, value)


def substitute_placeholders(
    value: Any, params: Mapping[str, Any], *, _ancestors: frozenset[int] = frozenset()
) -> Any:
    """Fill bare identifiers without interpreting other braces or shared aliases.

    Unresolved identifiers remain intact. A mapping/list containing itself is
    refused; sibling aliases are not cycles.
    """
    if isinstance(value, str):
        return _fill(value, params)
    if isinstance(value, (Mapping, list)):
        marker = id(value)
        if marker in _ancestors:
            raise RegistrarError(
                "extends: recipe template contains a cyclic reference "
                "(a mapping/list that contains itself)"
            )
        child_ancestors = _ancestors | {marker}
        if isinstance(value, Mapping):
            return {
                key: substitute_placeholders(v, params, _ancestors=child_ancestors)
                for key, v in value.items()
            }
        return [
            substitute_placeholders(v, params, _ancestors=child_ancestors)
            for v in value
        ]
    if isinstance(value, tuple):
        return tuple(
            substitute_placeholders(v, params, _ancestors=_ancestors) for v in value
        )
    return value
