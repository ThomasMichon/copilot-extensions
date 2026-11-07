"""Authoritative GitHub branch and PR-head observations."""

from __future__ import annotations

import json
import re
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from .base import ProviderError, PullResult


def observe_branch(provider, repo, branch, *, api_base="", token=None, run):
    owner, separator, name = repo.partition("/")
    if not owner or not separator or not name or "/" in name:
        raise ProviderError("Branch observation needs an exact owner/repository")
    branch = branch.removeprefix("refs/heads/")
    if not branch or branch.startswith("refs/") or any(ord(char) < 33 for char in branch):
        raise ProviderError("Branch observation needs a nonempty branch name")
    host = provider.authority_endpoint(api_base)
    proc = run(
        ["gh", "api", "--hostname", host, "--method", "GET",
         f"repos/{quote(owner, safe='')}/{quote(name, safe='')}/git/ref/heads/{quote(branch, safe='')}"],
        env=provider._env(token, host=host),
    )
    if proc.returncode:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise ProviderError(f"GitHub branch observation failed: {detail}")
    try:
        data = json.loads(proc.stdout)
        if not isinstance(data, dict) or data.get("ref") != "refs/heads/" + branch:
            raise ValueError("missing exact ref")
        commit = data.get("object")
        if not isinstance(commit, dict) or commit.get("type") != "commit":
            raise ValueError("missing commit object")
        sha = commit.get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", sha) or sha == "0" * 40:
            raise ValueError("invalid branch tip")
    except (ValueError, TypeError) as exc:
        raise ProviderError("GitHub branch observation did not return one exact valid branch tip") from exc
    return {"repository": repo, "branch": branch, "head_sha": sha.lower()}


def observe_head(provider, repo, number, *, api_base="", token=None, run):
    """Read the exact head and GitHub's HTTP Date in one response."""
    host = provider.authority_endpoint(api_base)
    proc = run(
        ["gh", "api", "--hostname", host, "--include", f"/repos/{repo}/pulls/{number}"],
        env=provider._env(token),
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise ProviderError(f"gh PR #{number} observation failed: {detail}")
    marker = "\n{"
    body_at = proc.stdout.find(marker)
    if body_at < 0:
        raise ProviderError(f"gh PR #{number} observation was malformed.")
    headers = proc.stdout[:body_at]
    body = proc.stdout[body_at + 1:]
    date_header = ""
    for line in headers.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() == "date":
            date_header = value.strip()
    try:
        data = json.loads(body)
        observed_at = parsedate_to_datetime(date_header).isoformat()
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ProviderError(
            f"gh PR #{number} observation lacked valid server evidence."
        ) from exc
    return PullResult(
        number=int(data.get("number", number)),
        head_sha=str((data.get("head") or {}).get("sha", "")),
        observed_at=observed_at,
    )
