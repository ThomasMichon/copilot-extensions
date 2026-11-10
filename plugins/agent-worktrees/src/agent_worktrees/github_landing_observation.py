"""Bounded server landing-policy reads through the selected credential owner."""

from __future__ import annotations

import json
import re
from urllib.parse import quote

from .github_credentials import CredentialObservationError


_CLASSIC_QUERY = """
query($owner:String!,$name:String!) {
  repository(owner:$owner,name:$name) {
    id nameWithOwner isArchived isDisabled viewerPermission
    defaultBranchRef {
      name target { oid }
      branchProtectionRule { id requiresApprovingReviews }
    }
  }
}
"""


def _response(selected, stage: str, arguments: list[str], expected: type):
    raw = selected.capture(stage, arguments)
    try:
        header, body = re.split(r"\r?\n\r?\n", raw, maxsplit=1)
        status = re.match(r"HTTP/\S+ ([0-9]{3})(?:\s|$)", header)
        value = json.loads(body)
        if status is None or status[1] != "200" or not isinstance(value, expected):
            raise ValueError
    except (ValueError, TypeError, RecursionError):
        raise CredentialObservationError(stage, "invalid-response") from None
    return value, header


def _classic(selected) -> dict:
    owner, name = selected.repository.split("/")
    value, _ = _response(selected, "classic-policy", [
        "api", "graphql", "--hostname", "github.com", "--include",
        "-f", "query=" + _CLASSIC_QUERY, "-f", "owner=" + owner, "-f", "name=" + name,
    ], dict)
    data = value.get("data")
    repository = data.get("repository") if isinstance(data, dict) else None
    errors = value.get("errors")
    if isinstance(errors, list) and any(
        isinstance(error, dict) and isinstance(error.get("type"), str)
        and error["type"] in {"FORBIDDEN", "INSUFFICIENT_SCOPES"}
        for error in errors
    ):
        raise CredentialObservationError("classic-policy", "insufficient-permission")
    if errors or not isinstance(repository, dict):
        raise CredentialObservationError("classic-policy", "access-unconfirmed")
    ref = repository.get("defaultBranchRef")
    if (
        not isinstance(repository.get("id"), str) or not repository["id"]
        or not isinstance(repository.get("nameWithOwner"), str)
        or repository["nameWithOwner"].casefold() != selected.repository.casefold()
        or type(repository.get("isArchived")) is not bool
        or type(repository.get("isDisabled")) is not bool
        or "viewerPermission" not in repository
        or repository.get("viewerPermission") is not None and (
            not isinstance(repository["viewerPermission"], str)
            or repository["viewerPermission"] not in {"ADMIN", "MAINTAIN", "WRITE", "TRIAGE", "READ"}
        )
        or not isinstance(ref, dict) or not isinstance(ref.get("name"), str) or not ref["name"]
        or len(ref["name"]) > 1024 or any(ord(char) < 32 or ord(char) == 127 for char in ref["name"])
        or not isinstance(ref.get("target"), dict)
        or not isinstance(ref["target"].get("oid"), str)
        or not re.fullmatch(r"[a-f0-9]{40,64}", ref["target"]["oid"])
        or "branchProtectionRule" not in ref
    ):
        raise CredentialObservationError("classic-policy", "target-unconfirmed")
    rule = ref["branchProtectionRule"]
    if rule is not None and (
        not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not rule["id"]
        or type(rule.get("requiresApprovingReviews")) is not bool
    ):
        raise CredentialObservationError("classic-policy", "invalid-response")
    return repository


def _rules(selected, branch: str, landing: dict) -> list[str]:
    kinds = []
    path = "/repos/" + selected.repository + "/rules/branches/" + quote(branch, safe="")
    for page in range(1, 9):
        value, header = _response(selected, "ruleset-policy", [
            "api", "--hostname", "github.com", "--include",
            "https://api.github.com" + path + f"?per_page=100&page={page}",
        ], list)
        if len(value) > 100:
            raise CredentialObservationError("ruleset-policy", "invalid-response")
        for rule in value:
            if not isinstance(rule, dict) or not isinstance(rule.get("type"), str):
                raise CredentialObservationError("ruleset-policy", "invalid-response")
            kinds.append(rule["type"])
            if rule["type"] == "pull_request":
                landing["observed_pr_requirement"] = True
        relations = []
        for line in re.findall(r"(?im)^link:\s*(.+)$", header):
            for link in line.strip().split(","):
                match = re.fullmatch(
                    r'<https://api\.github\.com/[^>\s]+>;\s*rel="(next|prev|first|last)"',
                    link.strip(),
                )
                if match is None:
                    raise CredentialObservationError("ruleset-policy", "invalid-pagination")
                relations.append(match[1])
        if "next" not in relations:
            landing["rulesets"] = "complete"
            landing["rule_count"] = len(kinds)
            return kinds
    raise CredentialObservationError("ruleset-policy", "page-limit")


def observe(selected, landing: dict) -> None:
    initial = _classic(selected)
    ref = initial["defaultBranchRef"]
    classic = ref["branchProtectionRule"]
    landing.update(
        branch=ref["name"], classic="absent" if classic is None else "present",
        writable=initial["viewerPermission"] in {"ADMIN", "MAINTAIN", "WRITE"},
        repository_active=not initial["isArchived"] and not initial["isDisabled"],
    )
    if classic is not None and classic["requiresApprovingReviews"]:
        landing["observed_pr_requirement"] = True
    kinds = _rules(selected, ref["name"], landing)
    if _classic(selected) != initial:
        raise CredentialObservationError("classic-policy", "target-changed")
    if landing["observed_pr_requirement"] is True:
        landing.update(status="observed", mode="pull-request")
    elif (
        classic is None and landing["writable"] and landing["repository_active"]
        and set(kinds) <= {"deletion", "non_fast_forward"}
    ):
        landing.update(status="observed", mode="direct", observed_pr_requirement=False)
    else:
        landing.update(status="unknown", reason="ordinary-direct-publication-unconfirmed")
