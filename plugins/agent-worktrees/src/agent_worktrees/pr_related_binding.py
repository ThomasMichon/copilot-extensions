"""Explicit related-repository PR bindings do not require a local checkout."""

from __future__ import annotations


def parse_repository(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("pr.repository must be a provider repository slug")
    value = value.strip()
    if value and (value.count("/") != 1 or any(not part or part in (".", "..") for part in value.split("/"))
                  or any(ord(character) < 32 for character in value)):
        raise ValueError("pr.repository must be owner/name or project/repository")
    return value


def remember_checkout_free_pr(bindings, name, machine_repo, related_pr):
    from . import config as cfg
    if "pr" in machine_repo:
        raw = cfg._deep_merge({"pr": related_pr}, machine_repo)
        bindings[name] = cfg._parse_pr(raw.get("pr"))


def resolve_related_binding(config, repo_slug, *, include_control_plane=True):
    from . import config as cfg
    entries = cfg._control_plane_related_pr_map() if include_control_plane else {}
    targets = dict(config.repos)
    targets.update({
        name: cfg.RepoConfig(anchor="", worktree_root="", default_branch="", pr=pr)
        for name, pr in getattr(config, "checkout_free_pr", {}).items()
    })
    matches = [(name, value) for name, value in targets.items()
               if value.pr.repository == repo_slug]
    matches.extend((name, raw) for name, raw in entries.items() if name not in targets
                   and parse_repository(raw.get("repository", "")) == repo_slug)
    if len(matches) > 1:
        return None, "", "Multiple related definitions claim the same PR repository"
    if not matches:
        return None, "", ""
    name, raw = matches[0]
    if isinstance(raw, cfg.RepoConfig):
        if raw.pr.provider not in ("azure-devops", "github", "gitea"):
            return None, name, "The target PR binding needs an explicit supported provider"
        if raw.pr.provider != "github" and not raw.pr.api_base:
            return None, name, "The target PR binding needs its own hosting endpoint"
        return raw, name, ""
    if raw.get("provider") not in ("azure-devops", "github", "gitea"):
        return None, name, "The related PR binding needs an explicit supported provider"
    if raw["provider"] != "github" and not raw.get("api_base"):
        return None, name, "The related PR binding needs its own hosting endpoint"
    pr = cfg._parse_pr(raw)
    return cfg.RepoConfig(anchor="", worktree_root="", default_branch="", pr=pr), name, ""
