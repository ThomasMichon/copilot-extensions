"""GitHub query shapes and repository-slug syntax used by the CLI."""

from __future__ import annotations

import argparse
import re

PR_URL_NUMBER_RE = re.compile(r"/pull/(\d+)\s*$")

STATUS_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) { "
    "repository(owner: $owner, name: $name) { "
    "pullRequest(number: $number) { "
    "number title url state isDraft mergeable reviewDecision "
    "} } }"
)

WATCH_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) { "
    "repository(owner: $owner, name: $name) { "
    "pullRequest(number: $number) { "
    "state merged mergeable reviewDecision headRefOid updatedAt "
    "commits(last: 1) { nodes { commit { statusCheckRollup { state } } } } "
    "} } }"
)


def parse_repo_slug(value: str) -> tuple[str, str]:
    owner, sep, name = value.partition("/")
    if not sep or not owner or not name:
        raise argparse.ArgumentTypeError("repo must be 'owner/name'")
    return owner, name


def validate_repo_slug(value: str) -> str:
    parse_repo_slug(value)
    return value
