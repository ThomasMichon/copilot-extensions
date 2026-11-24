"""Source-owned GitHub query shape and bounded, non-secret variables."""

from __future__ import annotations

from dataclasses import dataclass
import re

API_BASE = "https://api.github.com"
ENDPOINT = API_BASE + "/graphql"
MAX_MEMBERS = 10
PAGE_SIZE = 100
MAX_RECORDS = 20_000
COLLECTIONS = ("labels", "reviews", "checks")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100}")
_PAGE = "totalCount pageInfo { hasNextPage endCursor }"
_MEMBER = """
  pINDEX: pullRequest(number: $nINDEX) {
    number state merged mergeable headRefOid baseRefOid baseRefName updatedAt title isDraft
    author { login }
    labels(first: 100, after: $labelsINDEX) @include(if: $readLabelsINDEX) {
      PAGE nodes { id name }
    }
    reviews(first: 100, after: $reviewsINDEX) @include(if: $readReviewsINDEX) {
      PAGE nodes { id fullDatabaseId state submittedAt author { login } commit { oid } }
    }
    commits(last: 1) { nodes { commit { oid statusCheckRollup {
      contexts(first: 100, after: $checksINDEX) @include(if: $readChecksINDEX) {
        PAGE nodes {
          __typename
          ... on StatusContext { id context state description targetUrl createdAt }
          ... on CheckRun { id name status conclusion detailsUrl url summary title }
        }
      }
    } } } }
  }
""".replace("PAGE", _PAGE)


@dataclass(frozen=True)
class PageRequest:
    number: int
    labels: str | None | bool = None
    reviews: str | None | bool = None
    checks: str | None | bool = None


def validate_selection(repository: str, numbers: object, api_base: str = API_BASE) -> tuple[int, ...]:
    if (
        api_base != API_BASE or not isinstance(repository, str)
        or _REPOSITORY.fullmatch(repository) is None
        or repository.split("/")[1] in (".", "..")
        or not isinstance(numbers, (tuple, list)) or not 1 <= len(numbers) <= MAX_MEMBERS
        or any(type(number) is not int or not 1 <= number <= 2_147_483_647 for number in numbers)
        or len(set(numbers)) != len(numbers)
    ):
        raise ValueError("snapshot batch requires 1..10 distinct GitHub.com PRs in one repository")
    return tuple(numbers)


def query(repository: str, requests: tuple[PageRequest, ...]) -> dict[str, object]:
    """Only member cardinality changes source text; all caller data is variables."""
    validate_selection(repository, [request.number for request in requests])
    owner, name = repository.split("/")
    declarations = ["$owner: String!", "$name: String!"]
    variables: dict[str, object] = {"owner": owner, "name": name}
    selections = []
    for index, request in enumerate(requests):
        declarations.append(f"$n{index}: Int!")
        variables[f"n{index}"] = request.number
        for collection in COLLECTIONS:
            cursor = getattr(request, collection)
            if cursor is not None and cursor is not False and (
                not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024
                or not cursor.isascii() or any(not char.isprintable() for char in cursor)
            ):
                raise ValueError("invalid snapshot batch collection cursor")
            flag = "read" + collection.title() + str(index)
            declarations.extend([f"${collection}{index}: String", f"${flag}: Boolean!"])
            variables[collection + str(index)] = cursor if isinstance(cursor, str) else None
            variables[flag] = cursor is not False
        selections.append(_MEMBER.replace("INDEX", str(index)))
    document = (
        "query PRSnapshotBatch(" + ", ".join(declarations) + ") {\n"
        "rateLimit { cost remaining resetAt }\n"
        "repository(owner: $owner, name: $name) { nameWithOwner\n"
        + "".join(selections) + "\n} }"
    )
    return {"query": document, "variables": variables}


def contract() -> dict[str, object]:
    return {
        "protocol": "agent-worktrees.pr-snapshot-batch/v1",
        "command": ["pr-watch", "snapshot-batch"], "providers": ["github"],
        "api_bases": [API_BASE], "credential": "observe/per-request",
        "max_members": MAX_MEMBERS, "page_size": PAGE_SIZE,
        "max_query_nodes": 8192, "expected_query_cost": 1,
        "max_requests": 64, "capture_bytes": 1_048_576,
        "requires": ["curl"], "requires_binary_qualification": True,
        "effects": "query-only", "scalar_default": "unchanged",
        "caller_backoff_persistence_required": True,
    }
