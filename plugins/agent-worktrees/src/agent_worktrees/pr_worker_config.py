"""Portable read/check policy for an explicitly selected worker workspace."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from urllib.parse import quote, urlsplit

from .config import RepoConfig
from .pr_observation_config import ObservationConfig, parse_observation
from .pr_publication_config import PublicationChecksConfig, _ssh_url, parse_publication_checks
from .pr_publication_labels import origin_dict


def _publication(policy: PublicationChecksConfig) -> dict:
    return {
        "features": {"self_review": policy.self_review, "scope_report": policy.scope_report},
        "review_lenses_path": policy.review_lenses_path,
        "allowed_paths": None if policy.allowed_paths is None else list(policy.allowed_paths),
        "guidance": policy.guidance,
        "ssh_url": policy.ssh_url,
    }


def _observation(policy: ObservationConfig | None) -> dict | None:
    if policy is None:
        return None
    return {
        "enabled": policy.enabled,
        "features": {key: getattr(policy, key) for key in ("reviews", "comments", "policies", "builds")},
        "requirements": {
            key: getattr(policy, key) for key in (
                "minimum_approvals", "non_author", "approval_after_push", "required_reviewers",
                "resolved_comments", "no_blocking_votes",
            )
        },
        "policy_rules": [asdict(rule) for rule in policy.policy_rules],
        "guidance": policy.guidance,
    }


def matches_remote(remote: str, repository: str, pr, workspace: str) -> bool:
    """Bind local checks to the selected provider's full publication destination."""
    from . import providers
    from .pr_publish import _identity
    from .providers.pr_source import same_repository

    if "://" in remote and urlsplit(remote).scheme not in ("https", "http", "ssh"):
        return False
    endpoint = providers.get_provider(pr.provider).authority_endpoint(pr.api_base)
    if pr.provider == "github":
        expected = f"https://{endpoint}/{repository}"
    elif pr.provider == "azure-devops":
        project, name = repository.split("/", 1)
        expected = f"{endpoint.rstrip('/')}/{quote(project, safe='')}/_git/{quote(name, safe='')}"
    elif pr.provider == "gitea":
        expected = f"{endpoint.rstrip('/')}/{repository}"
    else:
        raise ValueError("publication checks need a supported provider")
    actual = _identity(remote, workspace)
    if pr.provider == "github" and endpoint.removesuffix(":443") == "github.com":
        parsed = urlsplit(remote)
        if parsed.scheme == "ssh" and parsed.hostname == "ssh.github.com" and parsed.port == 443:
            actual = _identity("https://github.com" + parsed.path, workspace)
    if same_repository(actual, _identity(expected, workspace), pr.provider):
        return True
    policy = pr.publication_checks
    if pr.provider != "gitea" or policy is None or not policy.ssh_url:
        return False
    try:
        _ssh_url(remote)
    except ValueError:
        return False
    return actual == _identity(policy.ssh_url, workspace)


def _export_endpoint(pr) -> str:
    from . import providers

    raw = pr.api_base.strip()
    parsed = urlsplit(raw if "://" in raw or raw.startswith("//") else "//" + raw)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("worker policy endpoint must not contain credentials or query data")
    endpoint = providers.get_provider(pr.provider).authority_endpoint(raw)
    resolved = urlsplit("//" + endpoint if pr.provider == "github" else endpoint)
    if (
        not resolved.hostname or any(character.isspace() for character in endpoint)
        or resolved.username or resolved.password or resolved.query or resolved.fragment
        or (pr.provider != "github" and resolved.scheme not in ("https", "http"))
        or resolved.port == 0
    ):
        raise ValueError("worker policy needs a valid resolved provider endpoint")
    return endpoint


def export_config(
    repository: str, repo: RepoConfig, workspace: str, base: str | None, destination: Path,
) -> dict:
    """Export effective policy, never credentials, machine identity or merge grants."""
    if (
        not isinstance(workspace, str) or not workspace
        or any(character in workspace for character in "\0\r\n")
        or ".." in re.split(r"[/\\]", workspace)
        or not (PurePosixPath(workspace).is_absolute() or PureWindowsPath(workspace).is_absolute())
    ):
        raise ValueError("worker workspace must be an absolute path without parent traversal")
    remote = repo.remote
    if not isinstance(remote, str) or not remote or any(character.isspace() for character in remote):
        raise ValueError("worker policy needs an explicit remote name")
    selected_base = base or (f"{remote}/{repo.default_branch}" if repo.default_branch else "")
    if (
        not selected_base.startswith(remote + "/") or not selected_base[len(remote) + 1:]
        or any(character in selected_base for character in "\0\r\n")
    ):
        raise ValueError("worker policy needs --base <configured-remote>/<branch>")
    publication = repo.pr.publication_checks
    if publication is None and repo.pr.publication_origin is None:
        raise ValueError("this repository has no configured publication checks")
    pr = {
        key: getattr(repo.pr, key) for key in (
            "provider", "branch_update_strategy", "merge_strategy",
            "review_blocking", "dismiss_stale_reviews",
        )
    }
    pr.update(
        repository=repository,
        api_base=_export_endpoint(repo.pr),
        hold_labels=list(repo.pr.hold_labels),
        wip_title_prefixes=list(repo.pr.wip_title_prefixes),
        publication_checks=_publication(publication) if publication else None,
        publication_origin=origin_dict(repo.pr.publication_origin),
        observation=_observation(repo.pr.observation),
    )
    if (
        parse_publication_checks(pr["publication_checks"]) != publication
        or parse_observation(pr["observation"]) != repo.pr.observation
    ):
        raise ValueError("worker policy cannot preserve the effective configuration")
    data = {
        "repo_name": "pr-worker",
        "repos": {
            "pr-worker": {
                "anchor": workspace, "remote": remote,
                "default_branch": selected_base[len(remote) + 1:], "pr": pr,
            },
        },
    }
    payload = (json.dumps(data, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with destination.open("xb") as stream:
        stream.write(payload)
    return {
        "ok": True, "repository": repository, "base": selected_base,
        "config_file": str(destination.resolve()), "sha256": hashlib.sha256(payload).hexdigest(),
    }
