"""Pinned report-only gh routes; no CLI auxiliary discovery or raw diagnostics."""

import json

from ..pr_contract import PRDiff
from ..github_credentials import CredentialObservationError


def threads(capture, query, fields):
    """Run the existing thread query against the pinned GraphQL endpoint."""
    args = ["api", "--hostname", "github.com", "https://api.github.com/graphql", "-f", f"query={query}"]
    for key, value in fields.items():
        args += ["-F" if type(value) is int else "-f", f"variables[{key}]={value}"]
    try:
        data = json.loads(capture(args))
    except CredentialObservationError as error:
        if error.code != "access-unconfirmed":
            raise
        return {}, "scoped GitHub thread response unavailable"
    if not isinstance(data, dict) or data.get("errors"):
        return {}, "scoped GitHub thread response unavailable"
    return data, ""


def diff(capture, repo, number):
    """Read unified diff bytes without CLI auxiliary repository discovery."""
    try:
        raw = capture([
            "api", "--hostname", "github.com",
            f"https://api.github.com/repos/{repo}/pulls/{number}",
            "-H", "Accept: application/vnd.github.diff",
        ], binary=True)
    except CredentialObservationError as error:
        if error.code != "access-unconfirmed":
            raise
        return PRDiff(error="scoped GitHub diff response unavailable")
    return PRDiff(diff=raw.decode("utf-8"))
