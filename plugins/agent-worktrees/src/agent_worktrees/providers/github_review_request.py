"""One GitHub review-request transport, with optional explicit outcome evidence."""
from __future__ import annotations

import re
import subprocess

from ..pr_contract import ReviewNudgeResult
from ..pr_reviewer_request import outcome
from .base import ProviderError
from .pr_source import pull_payload


def _response(text: str) -> tuple[int, dict]:
    headers, separator, body = text.replace("\r\n", "\n").partition("\n\n")
    lines = headers.splitlines()
    match = re.fullmatch(r"HTTP/\S+ ([1-5][0-9]{2})(?: .*)?", lines[0]) if lines else None
    if not separator or match is None:
        raise ValueError("Missing authoritative GitHub response status")
    status = int(match[1])
    # A rejected request need not have a pull-request response body.
    return status, pull_payload(body) if 200 <= status < 300 else {}


def _identity(data: dict, repo: str, number: int) -> bool:
    base = data.get("base")
    repository = base.get("repo") if isinstance(base, dict) else None
    name = repository.get("full_name") if isinstance(repository, dict) else None
    return (
        type(data.get("number")) is int and data["number"] == number
        and isinstance(name, str) and name.casefold() == repo.casefold()
    )


def request(provider, repo, number, bot_login, *, api_base="", token=None, run,
            explicit: bool = False) -> tuple[ReviewNudgeResult, str]:
    """Retain the tracked transport contract; explicit callers inspect HTTP evidence."""
    host = provider.authority_endpoint(api_base)
    argv = [
        "gh", "api", "--hostname", host, "-X", "POST",
        f"repos/{repo}/pulls/{number}/requested_reviewers",
        "-f", f"reviewers[]={bot_login}",
    ]
    if explicit:
        argv.append("--include")
    proc = run(argv, env=provider._env(token, host=host))
    if explicit:
        try:
            status, data = _response(proc.stdout)
        except (ValueError, TypeError, ProviderError):
            return ReviewNudgeResult(supported=True, reviewer=bot_login,
                                     error="Review-request response is unconfirmed"), "unknown"
        if status in (400, 401, 403, 404, 405, 409, 410, 422):
            return ReviewNudgeResult(supported=True, reviewer=bot_login,
                                     error=f"GitHub rejected the review request (HTTP {status})"), "failed"
        if proc.returncode or not 200 <= status < 300 or not _identity(data, repo, number):
            return ReviewNudgeResult(supported=True, reviewer=bot_login,
                                     error="Review-request response is unconfirmed"), "unknown"
    elif proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        return ReviewNudgeResult(
            supported=True, requested=False, reviewer=bot_login,
            error=f"gh api requested_reviewers failed for {repo}#{number}: {detail}",
        ), "unknown"
    return ReviewNudgeResult(
        supported=True, requested=True, reviewer=bot_login,
        detail=(
            f"Requested a review from {bot_login}. GitHub processes this "
            "asynchronously (observed latency: minutes) -- poll pr-status "
            "or pr-watch afterward rather than assuming an immediate verdict."
        ),
    ), "accepted"


def target(provider, repo, number, *, reviewer="", api_base="", token=None, run):
    """Preflight one explicit active non-draft target, then make one request."""
    bot = provider.reviewer_login(reviewer)
    if not bot:
        return outcome(repo, number, provider=provider.name, status="no-op",
                       detail="No configured mapped automated reviewer; no request was made")
    try:
        host = provider.authority_endpoint(api_base)
        proc = run(
            ["gh", "api", "--hostname", host, f"repos/{repo}/pulls/{number}"],
            env=provider._env(token, host=host),
        )
        data = pull_payload(proc.stdout) if not proc.returncode else {}
        if (not _identity(data, repo, number) or data.get("state") != "open"
                or data.get("merged") is not False or data.get("draft") is not False):
            raise ValueError("Reviewer request needs authoritative same-target active non-draft evidence")
    except (ValueError, OSError, ProviderError, subprocess.SubprocessError) as exc:
        return outcome(repo, number, provider=provider.name, reviewer=bot, error=str(exc))
    try:
        result, status = request(
            provider, repo, number, bot, api_base=api_base, token=token, run=run, explicit=True,
        )
        return outcome(repo, number, provider=provider.name, reviewer=bot, status=status,
                       error=result.error, detail=result.detail)
    except (ValueError, OSError, ProviderError, subprocess.SubprocessError):
        return outcome(repo, number, provider=provider.name, reviewer=bot, status="unknown",
                       error="Review request may have been accepted; observe this PR before another attempt")
