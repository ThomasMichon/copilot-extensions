"""Pinned report-only gh routes; no CLI auxiliary discovery or raw diagnostics."""

import json

from ..pr_contract import PRDiff


def threads(capture, query, fields):
    """Run the existing thread query against the pinned GraphQL endpoint."""
    args = ["api", "--hostname", "github.com", "https://api.github.com/graphql", "-f", f"query={query}"]
    for key, value in fields.items():
        args += ["-F", f"{key}={value}"]
    data = json.loads(capture(args))
    if not isinstance(data, dict) or data.get("errors"):
        return {}, "scoped GitHub thread response unavailable"
    return data, ""


def diff(capture, repo, number):
    """Read unified diff bytes without CLI auxiliary repository discovery."""
    raw = capture([
        "api", "--hostname", "github.com",
        f"https://api.github.com/repos/{repo}/pulls/{number}",
        "-H", "Accept: application/vnd.github.diff",
    ], binary=True)
    return PRDiff(diff=raw.decode("utf-8"))
