"""Resolve a read-only ADO observation for a manually tracked PR."""

from dataclasses import replace
import re
from urllib.parse import unquote, urlsplit

from . import git_ops
from .config import PRConfig, RepoConfig
from .pr_worker_config import matches_remote
from .tracking import PRRecord


def observation_target(pr: PRRecord, repo: RepoConfig) -> tuple[PRConfig, str] | None:
    """Bind a hosted ADO URL to the configured org, or to the direct repo's Git remote."""
    policy = repo.pr
    raw = getattr(pr, "url", "")
    try:
        url = urlsplit(raw)
        if url.scheme != "https" or url.username or url.password or url.port not in (None, 443):
            return None
        host = url.hostname or ""
        if host == "dev.azure.com":
            org = url.path.lstrip("/").partition("/")[0]
            if not re.fullmatch(r"[a-zA-Z0-9-]+", org):
                return None
            endpoint = f"https://{host}/{org}"
        elif re.fullmatch(r"[a-zA-Z0-9-]+\.visualstudio\.com", host):
            endpoint = f"https://{host}"
        else:
            return None
        from .cleanup import _pr_claim_target

        candidate = replace(policy, provider="azure-devops", api_base=endpoint)
        target = _pr_claim_target(raw, candidate)
        if not target or target[2] != pr.number:
            return None
        components = [unquote(s, errors="strict") for s in target[1].split("/")]
        if any(not s or "/" in s or "\\" in s or s in (".", "..") for s in components):
            return None
        slug = "/".join(components)
        recorded = getattr(pr, "repo", "") or ""
        if "/" in recorded and recorded.casefold() != slug.casefold():
            return None
        if policy.provider == "azure-devops" and policy.api_base:
            if not matches_remote(raw.rsplit("/pullrequest/", 1)[0], slug, policy, repo.anchor):
                return None
        else:
            if (policy.enabled or policy.required or policy.api_base or policy.repository
                    or policy.token_env or policy.token_command or policy.provider not in
                    (PRConfig().provider, "azure-devops")):
                return None
            remote = git_ops.git("remote", "get-url", repo.remote, cwd=repo.anchor,
                                 check=False, isolated_repository=True)
            if remote.returncode or not matches_remote(remote.stdout.strip(), slug, candidate, repo.anchor):
                return None
        return candidate, slug
    except (ValueError, OSError):
        return None
