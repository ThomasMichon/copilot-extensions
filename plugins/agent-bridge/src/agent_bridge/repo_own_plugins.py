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

Why this exists (verified, dotfiles#905): Copilot ACP mode does not activate
repository ``enabledPlugins`` by itself. The bridge therefore treats those
settings as the desired-stack declaration and supplies every resolved payload
explicitly. Local marketplaces provide source directories; installed inventory
provides the fallback for remote marketplaces and other identities without a
resolvable local source.

Marketplace/plugin/settings resolution across the Copilot-native and Claude
conventions (native preferred) lives in the shared, vendored ``plugin_resolve``
lib; this module keeps only the bridge-specific complete-stack staging and
leak-safe policy.

Every function fails safe: any error yields an empty result, never an exception
into the dispatch path.
"""

from __future__ import annotations

import logging
from pathlib import Path

from plugin_resolve import (
    MarketplaceSourceKind,
    has_plugin_manifest,
    load_marketplace,
    local_marketplace_path,
    marketplace_source_kind,
    plugin_dir,
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


def _resolve_ref_dir(source: str, repo_roots: list[Path]) -> Path | None:
    """Resolve one ``PluginRef`` source to a local payload dir, or ``None``.

    Mirrors ``agent_codespaces.plugin_staging``'s local-marketplace lookup
    (first-wins across ``repo_roots``, shadowing-safe) without depending on
    that package -- both consume the same shared ``plugin_resolve``
    primitives. The **first** ``repo_roots`` entry whose settings declare
    ``marketplace`` at all *claims* it, regardless of source kind: a local
    declaration there resolves directly (success or failure stops here,
    never falling through to a different anchor's declaration of the same
    marketplace name); a non-local (remote) declaration there falls back to
    the installed-plugin payload instead, since there is no local source to
    read. Either way, a later anchor's own declaration of the same
    marketplace name is never consulted -- it would silently load a
    different or stale payload than the one the winning anchor actually
    declares. Only a marketplace **undeclared** by every ``repo_roots``
    entry falls back to the installed payload. Fail-safe -> ``None``.
    """
    name, marketplace = split_source(source)
    if not name or not marketplace:
        return None
    for root in repo_roots:
        try:
            settings = read_repo_settings(root)
        except Exception:  # pragma: no cover - defensive
            continue
        if marketplace not in settings.marketplaces:
            continue
        if marketplace_source_kind(marketplace, settings) is not MarketplaceSourceKind.LOCAL:
            # This anchor claims the marketplace name with a non-local
            # (remote) source -- there's nothing local to read here, but a
            # later anchor's own declaration of the same name must still
            # not be consulted. Fall back to the installed payload only.
            return _installed_dir(name, marketplace)
        # This anchor claims the marketplace name -- resolve exactly here,
        # success or failure, and never consult another anchor or the
        # installed inventory for this source.
        mp_root = local_marketplace_path(marketplace, settings, repo_dir=root)
        if mp_root is None:
            return None
        mp_root = mp_root.resolve()
        mp = load_marketplace(mp_root)
        if mp is None:
            return None
        payload = plugin_dir(mp, name)
        if payload is None:
            return None
        payload = payload.resolve()
        try:
            payload.relative_to(mp_root)
        except ValueError:
            log.warning(
                "Refusing plugin %s outside marketplace root %s: %s",
                source, mp_root, payload,
            )
            return None
        return payload if has_plugin_manifest(payload) else None
    return _installed_dir(name, marketplace)


def related_plugin_dir_args(
    repo: str | None, repo_roots: list[Path] | None = None,
) -> list[str]:
    """``--plugin-dir`` args for control-repo-declared related plugins.

    The **local-loopback** counterpart of a namespace-resolved (``codespace:``/
    ``container:``) target's ``extra_plugins`` staging: resolves every
    ``related_plugins_for_repo(repo)`` entry to a concrete payload dir on
    **this** machine (the dispatching machine, which a local-loopback target
    shares) rather than staging it anywhere -- there is nothing to copy, only
    to resolve. ``repo_roots`` defaults to every control-plane anchor
    (:func:`related_plugins.control_plane_anchors`). Fail-safe -> ``[]``; a
    source resolvable nowhere is skipped, never raised.
    """
    try:
        from .related_plugins import control_plane_anchors, related_plugins_for_repo

        refs = related_plugins_for_repo(repo)
        if not refs:
            return []
        roots = (
            list(repo_roots) if repo_roots is not None else control_plane_anchors()
        )
        args: list[str] = []
        resolved: list[str] = []
        unresolved: list[str] = []
        for ref in refs:
            payload = _resolve_ref_dir(ref.source, roots)
            if payload is None:
                unresolved.append(ref.source)
                continue
            args.extend(["--plugin-dir", str(payload)])
            resolved.append(ref.source)
        if resolved:
            log.info(
                "Resolved %d related-repo plugin(s) for local-loopback repo=%s "
                "-> --plugin-dir: %s",
                len(resolved), repo, resolved,
            )
        if unresolved:
            log.warning(
                "related-repo plugin(s) for repo=%s not resolvable locally -- "
                "NOT staged: %s",
                repo, unresolved,
            )
        return args
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("related-repo plugin resolution failed for repo=%s: %s", repo, exc)
        return []
