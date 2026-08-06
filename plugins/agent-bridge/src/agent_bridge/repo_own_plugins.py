"""Stage a repo's OWN enabled plugins as per-launch ``--plugin-dir`` args.

For a headless bridge launch of a repo's agent, make the plugins the repo declares
in its committed settings -- ``.github/copilot/settings.json`` (Copilot-native)
or, as a fallback, ``.claude/settings.json`` (Claude convention) -- available to
the dispatched ``copilot`` process **without globally enabling them on behalf of
one repo**. Staging is per-launch (``--plugin-dir``), scoped to that one process;
this module **never** writes ``~/.copilot/settings.json`` (``enabledPlugins`` /
``extraKnownMarketplaces``), never registers a marketplace, and never enables a
plugin globally.

Why this exists (verified, dotfiles#905): ``copilot`` loads a repo's enabled
plugins from its settings only when the plugin's files are **installed** on disk;
an *enabled-but-uninstalled* plugin (the fork / fresh machine case) is silently
skipped in headless mode. The primary guarantee is setup-install; this is the
per-launch backstop that points ``--plugin-dir`` at each such plugin's directory.
Installed plugins already load from settings and are **not** re-staged (avoids
double-loading a plugin).

Marketplace/plugin/settings resolution across the Copilot-native and Claude
conventions (native preferred) lives in the shared, vendored ``plugin_resolve``
lib; this module keeps only the bridge-specific installed-skip + leak-safe policy.

Every function fails safe: any error yields an empty result, never an exception
into the dispatch path.
"""

from __future__ import annotations

import logging
from pathlib import Path

from plugin_resolve import (
    has_plugin_manifest,
    resolve_repo_plugins,
    split_source,
)

log = logging.getLogger("agent-bridge")

_INSTALLED = Path("~/.copilot/installed-plugins").expanduser()


def _installed_dir(name: str, marketplace: str) -> Path | None:
    """The installed plugin dir (``installed-plugins/<mp>/<name>``) if present."""
    if not name or not marketplace:
        return None
    d = _INSTALLED / marketplace / name
    if has_plugin_manifest(d):
        return d
    return None


def repo_plugin_dir_args(anchor: str | Path | None) -> list[str]:
    """``--plugin-dir`` args for a repo's *enabled-but-uninstalled* plugins.

    ``anchor`` is the repo checkout root. Its committed plugin config is resolved
    **Copilot-native-first with a Claude fallback** by ``plugin_resolve`` -- from
    ``.github/copilot/settings.json`` (+ ``settings.local.json``) and, as a
    fallback, ``.claude/settings.json`` (+ ``.claude/settings.local.json``), with
    each enabled ``name@marketplace`` resolved to its on-disk source dir in a local
    (``directory`` / ``local``) marketplace such as the ``.ai`` standard. Plugins
    whose files are already **installed** on disk load from settings already and
    are skipped (no double-load). Leak-safe: never mutates global copilot config;
    a plugin that cannot be resolved to a local dir is reported, not staged.

    Returns a flat ``["--plugin-dir", <dir>, ...]`` list. Fail-safe -> ``[]``.
    """
    try:
        if anchor is None:
            return []
        anchor = Path(anchor)
        res = resolve_repo_plugins(anchor)

        args: list[str] = []
        staged: list[str] = []
        for source, plugin_dir in res.resolved.items():
            name, marketplace = split_source(source)
            if _installed_dir(name, marketplace) is not None:
                continue  # already loads from settings; don't double-stage
            args.extend(["--plugin-dir", str(plugin_dir)])
            staged.append(source)

        if staged:
            log.info(
                "Staged %d repo enabledPlugin(s) via --plugin-dir for %s "
                "(per-launch, not global-enabled): %s",
                len(staged), anchor, staged,
            )
        if res.unresolved:
            log.warning(
                "repo at %s enables %d plugin(s) not installed and not locally "
                "resolvable -- NOT staged and NOT global-enabled (install them at "
                "setup, or a remote-fetch backstop is needed): %s",
                anchor, len(res.unresolved), res.unresolved,
            )
        return args
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("repo own-plugin staging failed for %s: %s", anchor, exc)
        return []
