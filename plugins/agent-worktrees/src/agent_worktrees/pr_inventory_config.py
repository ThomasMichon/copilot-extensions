"""Passive selected-file inventory binding, without layered project/registry intake."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from .pr_related_binding import parse_repository

if TYPE_CHECKING:
    from .config import PRConfig


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key, value in node.value:
        name = loader.construct_object(key)
        if not isinstance(name, str) or name in result:
            raise ValueError("duplicate or nontext inventory configuration member")
        result[name] = loader.construct_object(value)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_binding(
    path: Path, repository: str, *, providers: tuple[str, ...] = ("github", "gitea"),
    allow_default_github_endpoint: bool = False,
) -> PRConfig:
    """Only explicit repos.<name>.pr fields select inventory provider policy."""
    from .config import _parse_pr
    if not path.is_absolute():
        raise ValueError("inventory configuration reference must be absolute")
    with path.open("rb") as stream:
        raw = stream.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("inventory configuration exceeds its byte bound")
    try:
        value = yaml.load(raw, Loader=_UniqueLoader)
    except (yaml.YAMLError, UnicodeError, RecursionError):
        raise ValueError("invalid inventory configuration") from None
    if not isinstance(value, dict) or not isinstance(value.get("repos"), dict):
        raise ValueError("explicit inventory repository bindings required")
    repos = value["repos"]
    if not 1 <= len(repos) <= 128:
        raise ValueError("invalid explicit repository cardinality")
    matches = []
    for binding in repos.values():
        if not isinstance(binding, dict):
            raise ValueError("invalid explicit repository binding")
        selected = binding.get("pr")
        if isinstance(selected, dict) and parse_repository(selected.get("repository", "")) == repository:
            endpoint = selected.get("api_base", "")
            if selected.get("provider") not in providers or not isinstance(endpoint, str) or (
                not endpoint and not (allow_default_github_endpoint and selected.get("provider") == "github")
            ):
                raise ValueError("inventory requires an explicit provider and endpoint")
            matches.append(_parse_pr(selected))
    if len(matches) != 1:
        raise ValueError("inventory requires one exact binding in the selected file")
    return matches[0]
