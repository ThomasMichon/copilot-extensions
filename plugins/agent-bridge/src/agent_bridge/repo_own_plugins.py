"""Stage a repo's OWN ``enabledPlugins`` as per-launch ``--plugin-dir`` args.

For a headless bridge launch of a repo's agent, make the plugins the repo declares
in its committed settings -- ``.github/copilot/settings.json`` (Copilot-native)
or, as a fallback, ``.claude/settings.json`` (Claude convention) -- available to
the dispatched ``copilot`` process **without globally enabling them on behalf of
one repo**. Staging is per-launch (``--plugin-dir``), scoped to that one process;
this module **never** writes ``~/.copilot/settings.json`` (``enabledPlugins`` /
``extraKnownMarketplaces``), never registers a marketplace, and never enables a
plugin globally.

Why this exists (verified, dotfiles#905): ``copilot`` loads a repo's
``enabledPlugins`` from its ``settings.json`` only when the plugin's files are
**installed** on disk; an *enabled-but-uninstalled* plugin (the fork / fresh
machine case) is silently skipped in headless mode. The primary guarantee is
setup-install; this is the per-launch backstop that points ``--plugin-dir`` at
each such plugin's directory. Installed plugins already load from ``settings.json``
and are **not** re-staged (avoids double-loading a plugin).

Every function fails safe: any error yields an empty result, never an exception
into the dispatch path.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("agent-bridge")

_INSTALLED = Path("~/.copilot/installed-plugins").expanduser()
# Repo settings-file conventions, **Copilot-native first, Claude fallback**.
# Copilot CLI resolves a repo's plugin config preferring its native
# ``.github/copilot/settings.json`` and falling back to the Claude
# ``.claude/settings.json`` (mirroring the documented ``.claude-plugin``
# marketplace-manifest fallback); own-plugin staging honors the same precedence
# so a repo declaring its plugins in either place is staged correctly. Ordered so
# a later file overrides an earlier one (native last => native wins).
_SETTINGS_RELS = (
    Path(".claude") / "settings.json",
    Path(".claude") / "settings.local.json",
    Path(".github") / "copilot" / "settings.json",
    Path(".github") / "copilot" / "settings.local.json",
)
# Candidate marketplace-manifest locations within a marketplace directory, in the
# CLI's own lookup order (see `copilot plugin marketplace add`). Covers both the
# legacy ``.github/plugin`` home and the ``.ai`` standard's ``.claude-plugin``.
_MARKETPLACE_MANIFEST_RELS = (
    Path("marketplace.json"),
    Path(".plugin") / "marketplace.json",
    Path(".github") / "plugin" / "marketplace.json",
    Path(".claude-plugin") / "marketplace.json",
)
# A plugin directory's manifest may live at the root or under ``.claude-plugin``
# (the ``.ai`` local-marketplace convention).
_PLUGIN_MANIFEST_RELS = (Path("plugin.json"), Path(".claude-plugin") / "plugin.json")


def _load_json(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _split_source(source: str) -> tuple[str, str]:
    """``name@marketplace`` -> ``(name, marketplace)`` (either may be empty)."""
    name, _, marketplace = (source or "").partition("@")
    return name.strip(), marketplace.strip()


def _has_plugin_manifest(d: Path) -> bool:
    """True when ``d`` holds a plugin manifest (root or ``.claude-plugin``)."""
    for rel in _PLUGIN_MANIFEST_RELS:
        try:
            if (d / rel).is_file():
                return True
        except OSError:
            pass
    return False


def _installed_dir(name: str, marketplace: str) -> Path | None:
    """The installed plugin dir (``installed-plugins/<mp>/<name>``) if present."""
    if not name or not marketplace:
        return None
    d = _INSTALLED / marketplace / name
    if _has_plugin_manifest(d):
        return d
    return None


def _local_marketplace_path(
    marketplace: str, marketplaces: dict, anchor: Path | None = None
) -> Path | None:
    """Resolve a **local** marketplace to its on-disk path (or ``None``).

    Recognizes the two local source spellings a repo may use:

    * ``{"source": {"source": "local", "path": ...}}`` -- the original spelling; and
    * ``{"source": {"source": "directory", "path": "./.ai"}}`` -- the ``.ai``
      **local plugin marketplace** standard (SPO.Core; the CLI's ``directory``
      source).

    A **relative** ``path`` is resolved against ``anchor`` (the repo checkout
    root), so a repo-scoped ``./.ai`` resolves correctly regardless of the daemon's
    cwd. Remote (github/git) marketplaces are not fetched here (a heavier, separate
    backstop) and yield ``None``.
    """
    if not isinstance(marketplaces, dict):
        return None
    entry = marketplaces.get(marketplace)
    src = entry.get("source") if isinstance(entry, dict) else None
    if isinstance(src, dict) and src.get("source") in ("local", "directory"):
        path = src.get("path")
        if isinstance(path, str) and path.strip():
            p = Path(path.strip())
            if not p.is_absolute() and anchor is not None:
                p = anchor / p
            return p
    return None


def _load_marketplace_manifest(mp_path: Path) -> dict | None:
    """Load a marketplace's ``marketplace.json`` from any supported location."""
    for rel in _MARKETPLACE_MANIFEST_RELS:
        manifest = _load_json(mp_path / rel)
        if manifest:
            return manifest
    return None


def _plugin_dir_in_marketplace(mp_path: Path, name: str) -> Path | None:
    """The plugin's source dir within a local marketplace, via its manifest."""
    manifest = _load_marketplace_manifest(mp_path)
    if not manifest:
        return None
    plugins = manifest.get("plugins")
    if not isinstance(plugins, list):
        return None
    for p in plugins:
        if isinstance(p, dict) and p.get("name") == name:
            sub = p.get("source")
            if isinstance(sub, str) and sub.strip():
                d = mp_path / sub.strip()
                if _has_plugin_manifest(d):
                    return d
    return None


def _load_repo_plugin_settings(anchor: Path) -> tuple[dict, dict]:
    """Merge a repo's ``enabledPlugins`` + ``extraKnownMarketplaces`` across the
    Copilot-native and Claude settings conventions (native preferred).

    Reads the ``_SETTINGS_RELS`` files in order (Claude first, native last), so
    native entries win on a key conflict; ``settings.local.json`` overrides
    ``settings.json`` within each convention. Returns ``(enabled, marketplaces)``.
    """
    enabled: dict = {}
    marketplaces: dict = {}
    for rel in _SETTINGS_RELS:
        data = _load_json(anchor / rel)
        if not data:
            continue
        en = data.get("enabledPlugins")
        if isinstance(en, dict):
            enabled.update(en)  # later file (native) wins
        mk = data.get("extraKnownMarketplaces")
        if isinstance(mk, dict):
            marketplaces.update(mk)  # later file (native) wins
    return enabled, marketplaces


def repo_plugin_dir_args(anchor: str | Path | None) -> list[str]:
    """``--plugin-dir`` args for a repo's *enabled-but-uninstalled* plugins.

    ``anchor`` is the repo checkout root. Its committed plugin config is read
    **Copilot-native-first with a Claude fallback** -- from
    ``.github/copilot/settings.json`` (+ ``settings.local.json``) and, as a
    fallback, ``.claude/settings.json`` (+ ``.claude/settings.local.json``). For
    each enabled ``name@marketplace`` whose files are **not** installed on disk,
    resolve a ``--plugin-dir`` target (a local-marketplace source dir today) and
    fold it in. Installed plugins load from settings already and are skipped (no
    double-load). Leak-safe: never mutates global copilot config; a plugin that
    cannot be resolved without a global mutation is reported, not staged.

    Returns a flat ``["--plugin-dir", <dir>, ...]`` list. Fail-safe -> ``[]``.
    """
    try:
        if anchor is None:
            return []
        anchor = Path(anchor)
        enabled, marketplaces = _load_repo_plugin_settings(anchor)
        if not enabled:
            return []

        args: list[str] = []
        staged: list[str] = []
        unresolved: list[str] = []
        for source, on in enabled.items():
            if not on or not isinstance(source, str):
                continue
            name, marketplace = _split_source(source)
            if not name or not marketplace:
                continue
            if _installed_dir(name, marketplace) is not None:
                continue  # already loads from settings.json; don't double-stage
            mp_path = _local_marketplace_path(marketplace, marketplaces, anchor)
            plugin_dir = (
                _plugin_dir_in_marketplace(mp_path, name)
                if mp_path is not None
                else None
            )
            if plugin_dir is not None:
                args.extend(["--plugin-dir", str(plugin_dir)])
                staged.append(source)
            else:
                unresolved.append(source)

        if staged:
            log.info(
                "Staged %d repo enabledPlugin(s) via --plugin-dir for %s "
                "(per-launch, not global-enabled): %s",
                len(staged), anchor, staged,
            )
        if unresolved:
            log.warning(
                "repo at %s enables %d plugin(s) not installed and not locally "
                "resolvable -- NOT staged and NOT global-enabled (install them at "
                "setup, or a remote-fetch backstop is needed): %s",
                anchor, len(unresolved), unresolved,
            )
        return args
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("repo own-plugin staging failed for %s: %s", anchor, exc)
        return []
