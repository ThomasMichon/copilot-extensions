"""Fixed personal-private creation and default-branch safeguard operations."""

from __future__ import annotations

import json
import re
from urllib.parse import quote

from .github_credentials import CredentialObservationError
from .github_landing_observation import _response

REQUIRED_RULES = {"deletion", "non_fast_forward"}


def repository(selected, value: dict | None = None) -> dict:
    """Qualify exact private personal identity without exposing provider bodies."""
    value = selected.json("repository", "/repos/" + selected.repository) if value is None else value
    owner = value.get("owner") if isinstance(value, dict) else None
    permissions = value.get("permissions") if isinstance(value, dict) else None
    branch = value.get("default_branch") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] <= 0
        or not isinstance(value.get("full_name"), str)
        or value["full_name"].casefold() != selected.repository.casefold()
        or value.get("private") is not True
        or not isinstance(owner, dict) or owner.get("type") != "User"
        or not isinstance(owner.get("login"), str)
        or owner["login"].casefold() != selected.login.casefold()
        or not isinstance(permissions, dict) or permissions.get("admin") is not True
        or value.get("archived") is not False or value.get("disabled") is not False
        or not isinstance(branch, str) or not branch or len(branch) > 1024
        or any(ord(char) < 32 or ord(char) == 127 for char in branch)
    ):
        raise CredentialObservationError("repository", "private-personal-identity-unconfirmed")
    return {
        "id": value["id"], "repository": selected.repository, "default_branch": branch,
        "private": True, "owner_kind": "user",
    }


def create_candidate(selected) -> str:
    """Reject visible existing targets; a 404 is not a claim of absence."""
    try:
        selected.json("repository", "/repos/" + selected.repository)
    except CredentialObservationError as error:
        if error.http_status == 404:
            return "not-visible"
        raise
    raise CredentialObservationError("repository", "already-exists")


def _post(selected, stage: str, path: str, body: dict) -> dict:
    raw = selected.capture(stage, [
        "api", "--hostname", "github.com", "--include",
        "--method", "POST", "https://api.github.com" + path, "--input", "-",
    ], input_text=json.dumps(body))
    try:
        header, content = re.split(r"\r?\n\r?\n", raw, maxsplit=1)
        status = re.match(r"HTTP/\S+ ([0-9]{3})(?:\s|$)", header)
        value = json.loads(content)
        if status is None or status[1] != "201" or not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError, RecursionError):
        raise CredentialObservationError(stage, "invalid-response") from None


def create(selected) -> dict:
    """Make exactly one authenticated-user creation request, never adoption."""
    value = _post(selected, "creation", "/user/repos", {
        "name": selected.repository.split("/")[1], "private": True, "auto_init": True,
    })
    return repository(selected, value)


def _policy(selected, expected: dict) -> tuple[dict, dict]:
    branch = quote(expected["default_branch"], safe="")
    classic_path = "/repos/" + selected.repository + "/branches/" + branch + "/protection"
    try:
        classic = selected.json("classic-policy", classic_path)
    except CredentialObservationError as error:
        if error.http_status != 404:
            raise
        classic = None
    protected = set()
    if classic is not None:
        admins = classic.get("enforce_admins")
        deletions = classic.get("allow_deletions")
        force = classic.get("allow_force_pushes")
        if (
            not isinstance(admins, dict) or type(admins.get("enabled")) is not bool
            or not isinstance(deletions, dict) or type(deletions.get("enabled")) is not bool
            or not isinstance(force, dict) or type(force.get("enabled")) is not bool
        ):
            raise CredentialObservationError("classic-policy", "invalid-response")
        if admins["enabled"]:
            if not deletions["enabled"]:
                protected.add("deletion")
            if not force["enabled"]:
                protected.add("non_fast_forward")
    rules, detail_cache = [], {}
    for page in range(1, 9):
        values, header = _response(selected, "ruleset-policy", [
            "api", "--hostname", "github.com", "--include",
            "https://api.github.com/repos/" + selected.repository + "/rules/branches/"
            + branch, "--method", "GET", "-f", "per_page=100", "-f", f"page={page}",
        ], list)
        if len(values) > 100:
            raise CredentialObservationError("ruleset-policy", "invalid-response")
        for rule in values:
            if not isinstance(rule, dict) or not isinstance(rule.get("type"), str):
                raise CredentialObservationError("ruleset-policy", "invalid-response")
            rules.append(rule)
        relations = []
        for line in re.findall(r"(?im)^link:\s*(.+)$", header):
            for link in line.strip().split(","):
                match = re.fullmatch(
                    r'<https://api\.github\.com/repos/' + re.escape(selected.repository)
                    + r'/rules/branches/' + re.escape(branch)
                    + r'\?per_page=100&page=([1-9][0-9]*)>;\s*rel="(next|prev|first|last)"',
                    link.strip(),
                )
                if match is None or match[2] == "next" and int(match[1]) != page + 1:
                    raise CredentialObservationError("ruleset-policy", "invalid-pagination")
                relations.append(match[2])
        if "next" not in relations:
            break
    else:
        raise CredentialObservationError("ruleset-policy", "page-limit")
    for rule in rules:
        if rule["type"] not in REQUIRED_RULES:
            continue
        ruleset_id = rule.get("ruleset_id")
        if type(ruleset_id) is not int or ruleset_id <= 0 or rule.get("ruleset_source_type") != "Repository":
            raise CredentialObservationError("ruleset-policy", "source-unconfirmed")
        if ruleset_id not in detail_cache:
            detail_cache[ruleset_id] = selected.json(
                "ruleset-policy", f"/repos/{selected.repository}/rulesets/{ruleset_id}",
            )
        detail = detail_cache[ruleset_id]
        if (
            type(detail.get("id")) is not int or detail["id"] != ruleset_id or detail.get("target") != "branch"
            or detail.get("source_type") != "Repository"
            or not isinstance(detail.get("source"), str)
            or detail["source"].casefold() != selected.repository.casefold()
            or detail.get("enforcement") != "active"
            or not isinstance(detail.get("bypass_actors"), list)
            or not isinstance(detail.get("rules"), list)
        ):
            raise CredentialObservationError("ruleset-policy", "enforcement-unconfirmed")
        if not detail["bypass_actors"] and any(
            isinstance(item, dict) and item.get("type") == rule["type"] for item in detail["rules"]
        ):
            protected.add(rule["type"])
    return (
        {"status": "verified" if REQUIRED_RULES <= protected else "missing",
         "rules": sorted(protected), "default_branch": expected["default_branch"]},
        {"classic": classic, "rules": rules, "details": detail_cache},
    )


def safeguards(selected, expected: dict) -> dict:
    """Re-read complete effective policy and identity before claiming safeguards."""
    if repository(selected) != expected:
        raise CredentialObservationError("policy", "repository-changed")
    observation, evidence = _policy(selected, expected)
    if _policy(selected, expected)[1] != evidence or repository(selected) != expected:
        raise CredentialObservationError("policy", "policy-or-repository-changed")
    return observation


def protect(selected, expected: dict) -> None:
    """Add only active default-branch deletion and non-fast-forward safeguards."""
    _post(selected, "protection", "/repos/" + selected.repository + "/rulesets", {
        "name": "Knowledge default-branch safeguards", "target": "branch",
        "enforcement": "active", "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [{"type": kind} for kind in sorted(REQUIRED_RULES)],
    })
