"""Composition policy helpers for `knowledge_plugins`.

Kept in a separate module (rather than inline in `knowledge_plugins.py`) to
stay under that module's line-count guard; these two checks are otherwise
self-contained and have no dependency on the overlay read/write machinery.
"""

from __future__ import annotations

from plugin_resolve import split_source

from . import config as cfg

# A knowledge repo's own `<repo>-harness` plugin is a self-referential
# maintenance surface for *that* repo (same convention as other named-repo
# `-harness` adapters), not a generically graftable capability -- the same
# reasoning that keeps a `*-agent` plugin venue-scoped and never loaded
# centrally. Composing it into a different harness's session would introduce
# an extra sessionStart/agent surface authored for a different repository.
SELF_HARNESS_SUFFIX = "-harness"


def is_self_referential_harness_plugin(source: str, local_names: set[str]) -> bool:
    """True when `source` names a knowledge-local `*-harness` plugin."""
    plugin, marketplace = split_source(source)
    return marketplace in local_names and plugin.endswith(SELF_HARNESS_SUFFIX)


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
