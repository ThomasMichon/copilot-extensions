"""GitHub review-rule evidence for status and bounded mutation exceptions."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import quote

from .github_required_checks import (
    RequiredChecksError,
    ReviewExceptionIneligible,
    review_exception_evidence,
)


def pull_review_exception(
    provider, repo: str, number: int, *, expected_head_sha: str,
    hold_labels: tuple[str, ...] = (), wip_title_prefixes: tuple[str, ...] = (),
    api_base: str = "", token: str | None = None, invoke: Callable,
) -> tuple[bool, bool, str]:
    host = provider.authority_endpoint(api_base)
    try:
        review_exception_evidence(
            repo, number, host=host, expected_head_sha=expected_head_sha,
            hold_labels=hold_labels, wip_title_prefixes=wip_title_prefixes,
            invoke=lambda argv: invoke(argv, env=provider._env(token, host=host)),
        )
    except ReviewExceptionIneligible as exc:
        return False, False, str(exc)
    except RequiredChecksError as exc:
        return False, True, str(exc)
    return True, False, ""


def pull_review_gate(
    provider, repo: str, number: int, *, api_base: str = "", token: str | None = None,
    invoke: Callable,
) -> tuple[bool, bool | None]:
    """Live required-review status and ruleset actor-bypass capability."""
    host = provider.authority_endpoint(api_base)
    proc = invoke(
        ["gh", "pr", "view", str(number), "--repo", repo,
         "--json", "reviewDecision,baseRefName"],
        env=provider._env(token, host=host),
    )
    if proc.returncode != 0:
        return False, None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, None
    if data.get("reviewDecision") != "REVIEW_REQUIRED":
        return False, None

    base_ref = (data.get("baseRefName") or "").strip()
    if not base_ref:
        return True, None
    rproc = invoke(
        ["gh", "api", "--hostname", host,
         f"repos/{repo}/rules/branches/{quote(base_ref, safe='')}"],
        env=provider._env(token),
    )
    if rproc.returncode != 0:
        return True, None
    try:
        rules = json.loads(rproc.stdout)
    except json.JSONDecodeError:
        return True, None
    if not isinstance(rules, list):
        return True, None
    ruleset_ids = {
        rule.get("ruleset_id") for rule in rules
        if isinstance(rule, dict)
        and rule.get("type") == "pull_request"
        and rule.get("ruleset_id")
    }
    if not ruleset_ids:
        return True, None

    for ruleset_id in ruleset_ids:
        sproc = invoke(
            ["gh", "api", "--hostname", host, f"repos/{repo}/rulesets/{ruleset_id}"],
            env=provider._env(token),
        )
        if sproc.returncode != 0:
            continue
        try:
            ruleset = json.loads(sproc.stdout)
        except json.JSONDecodeError:
            continue
        if isinstance(ruleset, dict) and ruleset.get("current_user_can_bypass") in (
            "always", "pull_requests_only",
        ):
            return True, True
    return True, False
