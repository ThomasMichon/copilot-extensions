"""Stage a repo's OWN ``enabledPlugins`` as per-launch ``--plugin-dir`` args.

For a headless bridge launch of a repo's agent, make the plugins the repo declares
in its ``.github/copilot/settings.json`` ``enabledPlugins`` available to the
dispatched ``copilot`` process **without globally enabling them on behalf of one
repo**. Staging is per-launch (``--plugin-dir``), scoped to that one process; this
module **never** writes ``~/.copilot/settings.json`` (``enabledPlugins`` /
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
_SETTINGS_REL = Path(".github") / "copilot" / "settings.json"
_MARKETPLACE_REL = Path(".github") / "plugin" / "marketplace.json"


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


def _installed_dir(name: str, marketplace: str) -> Path | None:
    """The installed plugin dir (``installed-plugins/<mp>/<name>``) if present."""
    if not name or not marketplace:
        return None
    d = _INSTALLED / marketplace / name
    try:
        if (d / "plugin.json").is_file():
            return d
    except OSError:
        pass
    return None


def _local_marketplace_path(marketplace: str, marketplaces: dict) -> Path | None:
    """Resolve a ``local``-source marketplace to its on-disk path (or ``None``).

    Only ``{"source": {"source": "local", "path": ...}}`` entries are resolved
    here -- a remote (github/git) marketplace is not fetched (that would be a
    heavier, separate backstop); such plugins are reported as unresolved rather
    than triggering any global mutation.
    """
    if not isinstance(marketplaces, dict):
        return None
    entry = marketplaces.get(marketplace)
    src = entry.get("source") if isinstance(entry, dict) else None
    if isinstance(src, dict) and src.get("source") == "local":
        path = src.get("path")
        if isinstance(path, str) and path.strip():
            return Path(path.strip())
    return None


def _plugin_dir_in_marketplace(mp_path: Path, name: str) -> Path | None:
    """The plugin's source dir within a local marketplace, via its manifest."""
    manifest = _load_json(mp_path / _MARKETPLACE_REL)
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
                try:
                    if (d / "plugin.json").is_file():
                        return d
                except OSError:
                    pass
    return None


def repo_plugin_dir_args(anchor: str | Path | None) -> list[str]:
    """``--plugin-dir`` args for a repo's *enabled-but-uninstalled* plugins.

    ``anchor`` is the repo checkout root (its committed
    ``.github/copilot/settings.json`` is read). For each enabled
    ``name@marketplace`` whose files are **not** installed on disk, resolve a
    ``--plugin-dir`` target (a local-marketplace source dir today) and fold it in.
    Installed plugins load from ``settings.json`` already and are skipped (no
    double-load). Leak-safe: never mutates global copilot config; a plugin that
    cannot be resolved without a global mutation is reported, not staged.

    Returns a flat ``["--plugin-dir", <dir>, ...]`` list. Fail-safe -> ``[]``.
    """
    try:
        if anchor is None:
            return []
        anchor = Path(anchor)
        settings = _load_json(anchor / _SETTINGS_REL)
        if not settings:
            return []
        enabled = settings.get("enabledPlugins")
        if not isinstance(enabled, dict):
            return []
        marketplaces = settings.get("extraKnownMarketplaces") or {}

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
            mp_path = _local_marketplace_path(marketplace, marketplaces)
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
