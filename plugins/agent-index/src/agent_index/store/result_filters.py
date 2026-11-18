"""Pure metadata eligibility shared by hydrated retrieval paths."""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from typing import Any, TypedDict

from agent_index.store.repo_filter import repo_matches
from agent_index.store.store import SearchResult


class SearchFilters(TypedDict, total=False):
    source: str | None
    language: str | None
    file_path_glob: str | None
    repo: str | None
    labels: list[str] | None
    camera: list[str] | None
    voice: list[str] | None


def _facet_values(metadata: dict[str, Any] | None, key: str) -> set[str]:
    """Normalize scalar and multi-valued metadata facets."""
    if not metadata:
        return set()
    val = metadata.get(key)
    if val is None:
        return set()
    if isinstance(val, list):
        return {str(v) for v in val}
    return {str(val)}


_FACET_KEYS = ("labels", "camera", "voice")


def _apply_filters(
    results: list[SearchResult],
    *,
    source: str | None = None,
    language: str | None = None,
    file_path_glob: str | None = None,
    repo: str | None = None,
    labels: list[str] | None = None,
    camera: list[str] | None = None,
    voice: list[str] | None = None,
    repo_matcher: Callable[[str, str], bool] = repo_matches,
) -> list[SearchResult]:
    """Apply source prefixes, exact language, shell globs and OR-matched facets."""
    facets = {"labels": labels, "camera": camera, "voice": voice}
    if not any((source, language, file_path_glob, repo, *facets.values())):
        return results

    filtered = results
    if source:
        filtered = [r for r in filtered if r.source == source or r.source.startswith(f"{source}:")]
    if language:
        filtered = [r for r in filtered if r.language == language]
    if file_path_glob:
        filtered = [r for r in filtered if fnmatch.fnmatchcase(r.file_path, file_path_glob)]
    if repo:
        filtered = [r for r in filtered if repo_matcher(r.source, repo)]
    for key in _FACET_KEYS:
        wanted = facets[key]
        if wanted:
            wanted_set = set(wanted)
            filtered = [
                r for r in filtered if wanted_set & _facet_values(r.metadata, key)
            ]
    return filtered
