"""Resolve a repo's OWN enabled plugins for AHP client-contribution.

Mirrors ``agent-bridge``'s ``repo_own_plugins.py`` (dotfiles#905): an AHP
session, like an ACP launch, does not activate a repository's committed
``enabledPlugins`` by itself -- ``copilotd``'s ``createSession`` carries no
instructions/plugin field a client can rely on ambient discovery for. This
module resolves the same repository settings agent-bridge does
(``.github/copilot/settings.json`` + ``.claude/settings.json`` fallback, via
the shared vendored ``plugin_resolve`` lib) and returns concrete on-disk plugin
directories, so the AHP provider can **contribute them explicitly** as
``createSession``'s ``activeClient.customizations`` -- the AHP-native
equivalent of agent-bridge's ``--plugin-dir`` staging for a standalone launch.

Unlike agent-bridge (which spawns its own ``copilot`` process and can pass
``--plugin-dir`` on its command line), an AHP client never controls the
process ``copilotd`` spawns -- so instead of flag args, this module hands back
the resolved ``(source, directory)`` pairs for ``ahp_provider.py`` to serve
over the AHP client-contributed-plugins wire protocol (reverse
``resourceList``/``resourceRead``; see ``copilot-host``'s
``client_plugins.rs``).

Every function fails safe: any error yields an empty result, never an
exception into the AHP session-create path.
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

log = logging.getLogger("worktree-manager")

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


def resolve_repo_plugin_dirs(anchor: str | Path | None) -> list[tuple[str, Path]]:
    """Resolve a repo's explicitly activated plugin stack to on-disk dirs.

    ``anchor`` is the repo checkout root. Its committed plugin config is
    resolved **Copilot-native-first with a Claude fallback** by
    ``plugin_resolve`` -- from ``.github/copilot/settings.json`` (+
    ``settings.local.json``) and, as a fallback, ``.claude/settings.json`` (+
    ``.claude/settings.local.json``), with each enabled ``name@marketplace``
    resolved to its on-disk source dir in a local (``directory``/``local``)
    marketplace such as the ``.ai`` standard. A plugin from a local
    marketplace resolves to that source directory. An enabled plugin without
    a resolvable local source falls back to its installed payload
    (``~/.copilot/installed-plugins/<marketplace>/<name>``) when present.

    Returns a list of ``(source, directory)`` pairs, ``source`` being the
    declared ``name@marketplace`` identity (used to name the AHP
    customization). Fail-safe -> ``[]``.
    """
    try:
        if anchor is None:
            return []
        anchor = Path(anchor)
        settings = read_repo_settings(anchor)
        res = resolve_repo_plugins(anchor)

        pairs: list[tuple[str, Path]] = []
        staged_local: list[str] = []
        staged_installed: list[str] = []
        for source, plugin_dir in res.resolved.items():
            pairs.append((source, plugin_dir))
            staged_local.append(source)

        unavailable: list[str] = []
        installed_fallbacks: list[str] = []
        for source in res.unresolved:
            name, marketplace = split_source(source)
            installed_dir = _installed_dir(name, marketplace)
            if installed_dir is None:
                unavailable.append(source)
                continue
            pairs.append((source, installed_dir))
            staged_installed.append(source)
            if marketplace in settings.marketplaces:
                installed_fallbacks.append(source)

        staged = staged_local + staged_installed
        if staged:
            log.info(
                "Resolved %d repo enabledPlugin(s) for AHP contribution at %s "
                "(%d local source, %d installed payload): %s",
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
                "resolvable -- NOT contributed to the AHP session: %s",
                anchor, len(unavailable), unavailable,
            )
        return pairs
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("repo own-plugin AHP resolution failed for %s: %s", anchor, exc)
        return []
