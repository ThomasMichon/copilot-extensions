"""Derive update authentication sources from the same settings and repo registry."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from plugin_resolve import RepoPluginSettings, read_repo_settings

from . import bare_anchor_sync, config as cfg, git_ops
from .update_auth import Source, source


def settings(context: Path | None) -> RepoPluginSettings:
    enabled, marketplaces = {}, {}
    for filename in ("settings.json", "settings.local.json"):
        path = Path.home() / ".copilot" / filename
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        if not isinstance(data, dict):
            raise ValueError(f"user Copilot {filename} must be an object")
        for key, destination in (
            ("enabledPlugins", enabled),
            ("extraKnownMarketplaces", marketplaces),
        ):
            value = data.get(key, {})
            if not isinstance(value, dict):
                raise ValueError(f"user Copilot {key} must be an object")
            destination.update(value)
    if context is not None:
        repo = read_repo_settings(context)
        enabled.update(repo.enabled)
        marketplaces.update(repo.marketplaces)
    return RepoPluginSettings(enabled=enabled, marketplaces=marketplaces)


def marketplaces(context: Path | None) -> list[Source]:
    selected = settings(context)
    required = {ref.partition("@")[2] for ref, on in selected.enabled.items() if on}
    results = []
    for name, entry in selected.marketplaces.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("source", {}), dict):
            raise ValueError(f"invalid marketplace definition: {name}")
        definition = entry.get("source", {})
        if definition.get("source") != "github":
            continue
        slug = definition.get("repo", "")
        if not isinstance(slug, str):
            raise ValueError(f"invalid GitHub marketplace repository: {name}")
        item = source(
            f"marketplace:{name}",
            "https://github.com/" + slug,
            required=name in required or name == "copilot-extensions",
            cwd=context,
        )
        if item is None:
            raise ValueError(f"invalid GitHub marketplace source: {name}")
        results.append(item)
    return results


def remote_url(remote: str, anchor: Path) -> str:
    parsed = urlsplit(remote)
    if parsed.scheme or remote.startswith(("git@", "/", "\\")):
        return remote
    result = git_ops.git(
        "remote",
        "get-url",
        remote,
        cwd=anchor,
        isolated_repository=True,
        check=False,
        timeout=5,
        kill_tree=True,
    )
    if result.returncode == 2:
        return ""  # Git's documented missing-remote status; local-only anchors are valid.
    if result.returncode:
        raise git_ops.GitError(
            ["git", "remote", "get-url", remote], result.returncode, result.stderr
        )
    return result.stdout.strip()


def anchors() -> list[Source]:
    if not cfg.active_project():
        return []
    config = cfg.load_config()
    results = []
    seen = set()
    for repo in (config.repos or {}).values():
        if not repo.anchor or repo.anchor in seen or not (Path(repo.anchor) / ".git").exists():
            continue
        seen.add(repo.anchor)
        anchor = Path(repo.anchor)
        if bare_anchor_sync.branch_skip_reason(
            anchor, repo.default_branch, bare=bare_anchor_sync.is_bare(anchor)
        ):
            continue
        item = source(
            "anchor:" + repo.anchor,
            remote_url(repo.remote, Path(repo.anchor)),
            purpose="anchor",
            cwd=Path(repo.anchor),
        )
        if item is not None:
            results.append(item)
    return results
