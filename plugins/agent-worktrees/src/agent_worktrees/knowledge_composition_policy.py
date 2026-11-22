"""Composition policy helpers for `knowledge_plugins`.

Kept in a separate module (rather than inline in `knowledge_plugins.py`) to
stay under that module's line-count guard; these two checks are otherwise
self-contained and have no dependency on the overlay read/write machinery.
"""

from __future__ import annotations

from .knowledge_overlay import is_self_referential_harness_plugin as is_self_referential_harness_plugin

from . import config as cfg

def repo_config_for(
    loaded_config: cfg.Config | None, repo_name: str
) -> cfg.RepoConfig | None:
    """Resolve the exact or default `RepoConfig` for `repo_name`, if any.

    Tolerant of a `loaded_config` that isn't a full `cfg.Config` (tests pass a
    lightweight stand-in exposing only `knowledge_repo`) -- any attribute
    lookup failure just means "no repo config available", not an error.
    """
    if loaded_config is None:
        return None
    try:
        repo_config = getattr(loaded_config, "repos", {}).get(repo_name)
        if repo_config is not None:
            return repo_config
        return loaded_config.default_repo
    except (KeyError, AttributeError):
        return None
