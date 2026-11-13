"""Bounded ADO population discovery through the native observation credential owner."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from urllib.parse import quote, urlencode, urlsplit

from pr_filters import Expression, PROTOCOL
from pr_filters.acquisition import PROTOCOL as ACQUISITION_PROTOCOL, plan

from .base import ProviderError, run_cli
from .observation_credentials import ObservationCredentials
from .observation_inventory import MAX_BYTES, MAX_PAGES, PAGE_SIZE

MAX_REQUESTS = 64
MAX_PULLS = 1000
MAX_IDENTITIES = 128
ACQUISITION = {
    "protocol": ACQUISITION_PROTOCOL, "user_ids": "uuid-lowercase",
    "query_fields": {"from": "creatorId", "assigned": "reviewerId"},
    "max_query_branches": MAX_REQUESTS,
}
CAPABILITIES = {
    "protocol": PROTOCOL,
    "fields": ["repo", "from", "assigned", "age", "tags", "publication"],
    "groups": [],
    "age_fields": ["created"],
    "acquisition": ACQUISITION,
}
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class _Limit(Exception):
    pass


def contract() -> dict:
    return {
        "protocol": "agent-worktrees.pr-population/v1", "filter_protocol": PROTOCOL,
        "providers": {"azure-devops": {
            **CAPABILITIES, "assigned_semantics": "native reviewer user IDs",
            "identity_semantics": "canonical lowercase IdentityRef.id UUID",
            "tags_semantics": "native PR labels; separate bounded read",
        }},
        "unsupported_providers": ["github", "gitea"],
        "credential": "observe/per-request", "effects": "read-only",
        "max_pages": MAX_PAGES, "max_pulls": MAX_PULLS, "page_size": PAGE_SIZE,
        "max_requests": MAX_REQUESTS, "max_seconds": 60,
        "max_capture_bytes": MAX_BYTES + 8, "max_json_bytes": MAX_BYTES,
        "max_identities_per_pull": MAX_IDENTITIES, "max_tags_per_pull": MAX_IDENTITIES,
        "max_query_branches": MAX_REQUESTS,
        "query_rotation": "offset/v1",
    }


def validate(provider, repo, api_base, expression, max_pages, max_pulls):
    if provider != "azure-devops" or (
        not isinstance(repo, str) or repo.count("/") != 1 or len(repo) > 256
        or any(not part or part in (".", "..") for part in repo.split("/"))
        or any(ord(char) < 32 or ord(char) == 127 for char in repo)
        or type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES
        or type(max_pulls) is not int or not 1 <= max_pulls <= MAX_PULLS
    ):
        raise ValueError("unsupported bounded population scope")
    if not isinstance(api_base, str) or not 1 <= len(api_base) <= 2048:
        raise ValueError("population organization endpoint is invalid")
    endpoint = urlsplit(api_base)
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.query or endpoint.fragment
            or endpoint.username is not None or endpoint.password is not None
            or any(ord(char) < 33 or ord(char) == 127 for char in api_base)):
        raise ValueError("population requires an explicit HTTPS organization endpoint")
    endpoint.port
    if any(part in (".", "..") for part in endpoint.path.split("/")) or "%" in endpoint.path:
        raise ValueError("population requires a literal organization endpoint")
    expression.require(CAPABILITIES, {repo})

    return plan(expression, ACQUISITION)


def _read(url, credentials, deadline):
    if time.monotonic() >= deadline:
        raise ProviderError("population deadline exhausted")
    authorization = credentials.authorization()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderError("population deadline exhausted")
    result = run_cli(
        ["curl", "-q", "--globoff", "--silent", "--show-error", "--request", "GET",
         "--proto", "=https", "--max-redirs", "0", "--max-time", str(remaining),
         "--url", url, "--header", "@-", "--header", "Accept: application/json",
         "--write-out", "\n%{http_code}"],
        input_text="Authorization: " + authorization + "\n", timeout=remaining,
        max_stdout_bytes=MAX_BYTES + 8, max_stderr_bytes=8192,
    )
    if result.returncode or time.monotonic() >= deadline:
        raise ProviderError("population read unavailable")
    body, separator, status = result.stdout.rpartition("\n")
    if not separator or status != "200" or len(body.encode("utf-8")) > MAX_BYTES:
        raise ProviderError("population read unavailable")
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        raise ProviderError("population returned invalid bounded JSON") from None


def _instant(value):
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return instant.timestamp() if instant.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _identity(value):
    identity = value.get("id") if isinstance(value, dict) else None
    return identity.lower() if isinstance(identity, str) and _GUID.fullmatch(identity.lower()) else None


def _facts(row, repo):
    author = _identity(row.get("createdBy"))
    if isinstance(row.get("createdBy"), dict) and row["createdBy"].get("isContainer", False) is not False:
        author = None
    reviewers = row.get("reviewers")
    assigned = None
    if isinstance(reviewers, list) and len(reviewers) <= MAX_IDENTITIES and all(
        isinstance(item, dict) and _identity(item) is not None
        and item.get("isContainer", False) is False for item in reviewers
    ):
        assigned = sorted({_identity(item) for item in reviewers})
    draft = row.get("isDraft")
    return {
        "repo": repo, "from": [author] if author is not None else None,
        "assigned": assigned, "created_at": _instant(row.get("creationDate")),
        "activity_at": None, "tags": None,
        "publication": ("draft" if draft else "published") if type(draft) is bool else None,
    }


def _page(value, repo):
    if not isinstance(value, dict) or not isinstance(value.get("value"), list):
        raise ValueError("invalid population page")
    rows = value["value"]
    if type(value.get("count")) is not int or value["count"] != len(rows) or len(rows) > PAGE_SIZE:
        raise ValueError("incomplete population page")
    project, repository = repo.split("/")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or (
            type(row.get("pullRequestId")) is not int or row["pullRequestId"] <= 0
            or row["pullRequestId"] in seen or row.get("status") != "active"
        ):
            raise ValueError("invalid population member")
        native_repo = row.get("repository")
        native_project = native_repo.get("project") if isinstance(native_repo, dict) else None
        if not isinstance(native_project, dict) or (
            repository.casefold() not in {
                value.casefold() for value in (native_repo.get("name"), native_repo.get("id"))
                if isinstance(value, str)
            }
            or project.casefold() not in {
                value.casefold() for value in (native_project.get("name"), native_project.get("id"))
                if isinstance(value, str)
            }
        ):
            raise ValueError("population member outside repository scope")
        seen.add(row["pullRequestId"])
    return rows


def _labels(value):
    if not isinstance(value, dict) or not isinstance(value.get("value"), list):
        return None
    rows = value["value"]
    if (type(value.get("count")) is not int or value["count"] != len(rows)
            or len(rows) > MAX_IDENTITIES
            or any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
                   or not 1 <= len(item["name"]) <= 256
                   or any(ord(char) < 32 for char in item["name"]) for item in rows)):
        return None
    return sorted({item["name"] for item in rows})


def population(
    provider, repo: str, *, api_base: str, expression: Expression, credential_source,
    max_pages=4, max_pulls=200, deadline=None,
    query_offset=0,
) -> dict:
    queries = validate(provider, repo, api_base, expression, max_pages, max_pulls)
    if type(query_offset) is not int or not 0 <= query_offset < MAX_REQUESTS:
        raise ValueError("population query offset outside bounded rotation")
    start = query_offset % len(queries)
    queries = queries[start:] + queries[:start]
    deadline = time.monotonic() + 60 if deadline is None else min(deadline, time.monotonic() + 60)
    endpoint = api_base.rstrip("/")
    project, repository = repo.split("/")
    base = (endpoint + "/" + quote(project, safe="") + "/_apis/git/repositories/"
            + quote(repository, safe="") + "/pullRequests")
    credentials = ObservationCredentials(
        credential_source, provider=provider, api_base=endpoint, repository=repo,
        max_requests=MAX_REQUESTS,
    )
    result = {
        "schema": "agent-worktrees.pr-population", "version": 1,
        "provider": provider, "api_base": endpoint, "repo": repo,
        "filter_revision": expression.revision, "outcome": "partial", "pages": 0,
        "max_pages": max_pages, "max_pulls": max_pulls, "error": "page_limit", "pulls": [],
    }
    known = {}
    now = time.time()
    try:
        for query in queries:
            seen = set()
            page = 0
            while True:
                if result["pages"] >= max_pages:
                    raise _Limit("page_limit")
                parameters = {"searchCriteria.status": "active", "$top": PAGE_SIZE,
                              "$skip": page * PAGE_SIZE, "api-version": "7.1"}
                if query is not None:
                    key, identity = query
                    parameters["searchCriteria." + key] = identity
                rows = _page(_read(base + "?" + urlencode(parameters), credentials, deadline), repo)
                if seen.intersection(row["pullRequestId"] for row in rows):
                    raise ValueError("population page repeated")
                result["pages"] += 1
                if not rows:
                    break
                for row in rows:
                    number = row["pullRequestId"]
                    seen.add(number)
                    facts = _facts(row, repo)
                    if number not in known:
                        member = {"number": number, "facts": facts,
                                  "match": expression.evaluate(facts, now=now)}
                        known[number] = member
                        result["pulls"].append(member)
                        if len(known) >= max_pulls:
                            raise _Limit("pull_limit")
                    else:
                        # Overlapping query pages can straddle metadata changes.
                        accepted = known[number]["facts"]
                        for key, value in facts.items():
                            if accepted[key] != value:
                                accepted[key] = None
                page += 1
        result.update(outcome="complete", error="")
    except _Limit as error:
        result["error"] = str(error)
    except (OSError, ValueError, ProviderError, UnicodeError):
        result.update(outcome="partial" if result["pages"] else "unavailable",
                      error="read_unavailable")
    # Labels are a separate native collection. Missing or failed evidence stays unknown.
    for member in result["pulls"] if result["error"] != "read_unavailable" else []:
        try:
            labels = _labels(_read(
                base + f"/{member['number']}/labels?api-version=7.1", credentials, deadline,
            ))
            if labels is None:
                raise ValueError("population labels incomplete")
            member["facts"]["tags"] = labels
        except (OSError, ValueError, ProviderError, UnicodeError):
            result.update(outcome="partial", error="metadata_unavailable")
            break
    for member in result["pulls"]:
        member["match"] = expression.evaluate(member["facts"], now=now)
    # Activity is deliberately unsupported, not an incomplete advertised field.
    if any(any(value is None for key, value in member["facts"].items()
               if key != "activity_at") or member["match"] == "unknown"
           for member in result["pulls"]):
        result.update(outcome="partial", error=result["error"] or "metadata_incomplete")
    members, result["pulls"] = result["pulls"], []
    size = len(json.dumps(result, allow_nan=False).encode("utf-8")) + 64
    for member in members:
        size += len(json.dumps(member, allow_nan=False).encode("utf-8")) + 2
        if size > MAX_BYTES:
            result.update(outcome="partial", error="capture_limit")
            break
        result["pulls"].append(member)
    return result
