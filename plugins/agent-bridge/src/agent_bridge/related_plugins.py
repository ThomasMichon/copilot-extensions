"""Source *related-repo* plugins from the control-plane ``related.yaml``.

agent-bridge owns the **related-repo** plugin lane: given a dispatch target's
workspace repo, it returns the plugins the control plane declares for that
related repo (the ``plugins:`` block of the entry in
``<anchor>/.agent-worktrees/related.yaml``), so a namespace resolver can fold
them into the dispatched agent's launch. This is distinct from a CodeSpace's
own ``codespacePlugins`` (owned by agent-codespaces).

Kept **dependency-free of agent-worktrees** (which is not installed in the
bridge daemon venv): a minimal, guarded YAML read of the same committed files.
Every function fails safe -- any error yields an empty result, never an
exception into the dispatch path.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml

from .config import load_config
from .transport import PluginRef

log = logging.getLogger("agent-bridge")

_RELATED_REL = Path(".agent-worktrees") / "related.yaml"
_REPOS_YAML = Path("~/.agent-worktrees/repos.yaml").expanduser()


def _platform_keys() -> tuple[str, ...]:
    """Registry path keys to try for this platform, most-specific first."""
    if sys.platform == "win32":
        return ("windows",)
    # Linux / WSL share the posix path; try both spellings.
    return ("linux", "wsl")


def _load_yaml(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _registry_anchor(repo_name: str) -> Path | None:
    """Canonical on-disk anchor for a registered repo name, from repos.yaml."""
    data = _load_yaml(_REPOS_YAML)
    if not data:
        return None
    repos = data.get("repos")
    entries = repos if isinstance(repos, dict) else data
    entry = entries.get(repo_name) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        return None
    for key in _platform_keys():
        val = entry.get(key)
        if isinstance(val, str) and val.strip():
            return Path(val.strip())
    return None


def control_plane_anchors() -> list[Path]:
    """Anchors whose ``<anchor>/.agent-worktrees/related.yaml`` exists.

    Derived from the bridge's configured **topologies**: for each topology, try
    the ``machines_yaml``'s parent directory; if that worktree is gone (a stale
    config path), fall back to resolving the topology **name** via the
    agent-worktrees repos registry (``~/.agent-worktrees/repos.yaml``) to its
    canonical anchor. De-duplicated, order-preserving. Fail-safe -> ``[]``.
    """
    anchors: list[Path] = []
    seen: set[str] = set()

    def _add(anchor: Path | None) -> None:
        if anchor is None:
            return
        rel = anchor / _RELATED_REL
        try:
            if rel.is_file():
                key = str(anchor.resolve())
                if key not in seen:
                    seen.add(key)
                    anchors.append(anchor)
        except OSError:
            pass

    try:
        cfg = load_config()
        topologies = getattr(cfg, "topologies", {}) or {}
    except Exception:
        topologies = {}

    for name, profile in topologies.items():
        machines_yaml = getattr(profile, "machines_yaml", None)
        if machines_yaml:
            _add(Path(machines_yaml).parent)
        # Fall back to the canonical anchor for the topology's repo name, which
        # survives the stale-worktree case that a machines_yaml path can hit.
        _add(_registry_anchor(name))

    return anchors


def _entry_repos(entry: dict) -> list[str]:
    """The venue repos an entry maps to (locus.codespace/container ``repo``)."""
    locus = entry.get("locus")
    if not isinstance(locus, dict):
        return []
    repos: list[str] = []
    for venue in ("codespace", "container"):
        v = locus.get(venue)
        if isinstance(v, dict):
            r = v.get("repo")
            if isinstance(r, str) and r.strip():
                repos.append(r.strip())
    return repos


def plugin_name(source: str) -> str:
    """The plugin name from a source (``name@marketplace`` -> ``name``)."""
    return (source or "").strip().partition("@")[0].strip()


def is_harness_plugin(source: str) -> bool:
    """True for a ``<reponame>-harness[-*]`` plugin.

    Harness plugins are central-harness-only (they let an orchestrator cross-talk
    with a repo remotely) and must NEVER be propagated to a repo's venue. See the
    control-plane AGENTS.md "Plugin Naming & Propagation Convention". This is the
    hard guard that keeps a mis-declaration from leaking a harness plugin onto a
    CodeSpace.
    """
    name = plugin_name(source)
    return name.endswith("-harness") or "-harness-" in name


def _parse_plugin_items(raw: object) -> list[PluginRef]:
    """Parse a related entry's ``plugins`` list into PluginRefs (tolerant).

    Drops any ``*-harness*`` plugin: those are central-harness-only and must not
    be side-loaded into a venue, even if mis-declared here.
    """
    if not isinstance(raw, list):
        return []
    seen: dict[str, PluginRef] = {}
    for item in raw:
        if isinstance(item, str):
            source, enable = item.strip(), True
        elif isinstance(item, dict):
            source = str(item.get("source", "")).strip()
            enable = bool(item.get("enable", True))
        else:
            continue
        if not source:
            continue
        if is_harness_plugin(source):
            log.warning(
                "Refusing to propagate harness plugin '%s' to a venue "
                "(*-harness* plugins are central-harness-only)", source,
            )
            continue
        seen[source] = PluginRef(source=source, enable=enable)
    return list(seen.values())


def related_plugins_for_repo(
    repo: str | None, anchors: list[Path] | None = None
) -> list[PluginRef]:
    """Related-repo plugins the control plane side-loads for ``repo``.

    ``repo`` is a dispatched target's workspace repo (e.g.
    ``odsp-microsoft/odsp-web-codespaces``). It is matched (case-insensitive)
    against each related entry's ``locus.codespace.repo`` /
    ``locus.container.repo``; the first matching entry's ``plugins`` are
    returned. Fail-safe: unknown repo / missing files -> ``[]``.
    """
    if not repo:
        return []
    target = repo.strip().lower()
    for anchor in (anchors if anchors is not None else control_plane_anchors()):
        data = _load_yaml(anchor / _RELATED_REL)
        if not data:
            continue
        related = data.get("related")
        if not isinstance(related, dict):
            continue
        for entry in related.values():
            if not isinstance(entry, dict):
                continue
            if any(r.lower() == target for r in _entry_repos(entry)):
                return _parse_plugin_items(entry.get("plugins"))
    return []
