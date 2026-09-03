"""Stage a repo's OWN enabled plugins as per-launch ``--plugin-dir`` args.

For a headless bridge launch of a repo's agent, read the same repository settings
that declare its desired stack -- ``.github/copilot/settings.json``
(Copilot-native) or, as a fallback, ``.claude/settings.json`` (Claude
convention) -- resolve every enabled identity to a concrete payload directory,
and compose the complete launch explicitly through ``--plugin-dir``. Staging is
per-launch, scoped to that one process; this module **never** writes
``~/.copilot/settings.json`` (``enabledPlugins`` /
``extraKnownMarketplaces``), never registers a marketplace, and never enables a
plugin globally.

Why this exists (verified, dotfiles#905): an *enabled-but-uninstalled* plugin
(the fork / fresh machine case) is silently skipped in headless mode. In
addition, once a repository launch needs explicit ``--plugin-dir`` arguments
for local marketplaces, installed remote-marketplace payloads must also be
passed explicitly; relying on repository settings alone can omit them from the
ACP process. The primary guarantee is setup-install; this is the per-launch
backstop that makes the complete enabled stack explicit without mutating global
configuration.

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
    read_repo_settings,
    resolve_repo_plugins,
    split_source,
)

log = logging.getLogger("agent-bridge")

_INSTALLED = Path("~/.copilot/installed-plugins").expanduser()


def _installed_dir(name: str, marketplace: str) -> Path | None:
    """The installed plugin dir (``installed-plugins/<mp>/<name>``) if present."""
    if (
        not name
        or not marketplace
        or Path(name).name != name
        or Path(marketplace).name != marketplace
    ):
        return None
    d = _INSTALLED / marketplace / name
    try:
        d.resolve().relative_to(_INSTALLED.resolve())
    except ValueError:
        return None
    if has_plugin_manifest(d):
        return d
    return None


def repo_plugin_dir_args(anchor: str | Path | None) -> list[str]:
    """``--plugin-dir`` args for a repo's explicitly activated plugin stack.

    ``anchor`` is the repo checkout root. Its committed plugin config is resolved
    **Copilot-native-first with a Claude fallback** by ``plugin_resolve`` -- from
    ``.github/copilot/settings.json`` (+ ``settings.local.json``) and, as a
    fallback, ``.claude/settings.json`` (+ ``.claude/settings.local.json``), with
    each enabled ``name@marketplace`` resolved to its on-disk source dir in a local
    (``directory`` / ``local``) marketplace such as the ``.ai`` standard. A plugin
    from a local marketplace is staged from that source directory. An enabled
    plugin without a resolvable local source is staged from its installed
    payload when available. This is exhaustive because Copilot ACP launches
    ignore ``enabledPlugins`` and load plugin capabilities only from explicit
    ``--plugin-dir`` arguments. Leak-safe: never mutates global Copilot config;
    a plugin unavailable both locally and in the installed inventory is
    reported, not staged.

    Returns a flat ``["--plugin-dir", <dir>, ...]`` list. Fail-safe -> ``[]``.
    """
    try:
        if anchor is None:
            return []
        anchor = Path(anchor)
        settings = read_repo_settings(anchor)
        res = resolve_repo_plugins(anchor)

        args: list[str] = []
        staged_local: list[str] = []
        staged_installed: list[str] = []
        for source, plugin_dir in res.resolved.items():
            args.extend(["--plugin-dir", str(plugin_dir)])
            staged_local.append(source)

        unavailable: list[str] = []
        installed_fallbacks: list[str] = []
        for source in res.unresolved:
            name, marketplace = split_source(source)
            installed_dir = _installed_dir(name, marketplace)
            if installed_dir is None:
                unavailable.append(source)
                continue
            args.extend(["--plugin-dir", str(installed_dir)])
            staged_installed.append(source)
            if marketplace in settings.marketplaces:
                installed_fallbacks.append(source)

        staged = staged_local + staged_installed
        if staged:
            log.info(
                "Staged %d repo enabledPlugin(s) via --plugin-dir for %s "
                "(%d local source, %d installed payload; per-launch, not "
                "global-enabled): %s",
                len(staged), anchor, len(staged_local), len(staged_installed), staged,
            )
        if installed_fallbacks:
            log.info(
                "Used installed payload fallback for %d enabled plugin(s) "
                "without a resolvable local source: %s",
                len(installed_fallbacks), installed_fallbacks,
            )
        if unavailable:
            log.warning(
                "repo at %s enables %d plugin(s) not installed and not locally "
                "resolvable -- NOT staged and NOT global-enabled (install them at "
                "setup, or a remote-fetch backstop is needed): %s",
                anchor, len(unavailable), unavailable,
            )
        return args
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("repo own-plugin staging failed for %s: %s", anchor, exc)
        return []
