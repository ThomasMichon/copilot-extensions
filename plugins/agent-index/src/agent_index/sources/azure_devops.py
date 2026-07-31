"""Azure DevOps work items and pull requests source connector."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from agent_index.config import install_dir
from agent_index.sources.base import FileEntry
from agent_index.sources.good_citizen_http import GoodCitizenSession

_DEFAULT_API_BASE = "https://dev.azure.com"
_DEFAULT_TIMEOUT = 20.0
_DEFAULT_MIN_INTERVAL_S = 0.2
_DEFAULT_API_VERSION = "7.1"
_DEFAULT_COMMENT_API_VERSION = "7.1-preview.3"
_DEFAULT_BATCH_SIZE = 200
_DEFAULT_PAGE_SIZE = 100
_DEFAULT_OVERLAP_SECONDS = 300
_ADO_CONFIG_ENV = "AGENT_INDEX_ADO_CONFIG"

log = logging.getLogger(__name__)


class AzureDevOpsConnector:
    """Source connector for Azure DevOps work items and pull requests."""

    def __init__(
        self,
        source: str,
        *,
        token: str | None = None,
        api_base: str | None = None,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        min_interval_s: float = _DEFAULT_MIN_INTERVAL_S,
        changed_since: str | None = None,
        area_path: str | None = None,
        iteration_path: str | None = None,
        work_item_queries: list[dict[str, Any]] | None = None,
        pull_request_queries: list[dict[str, Any]] | None = None,
        config_path: str | Path | None = None,
        pull_request_status: str | None = None,
        repository_id: str | None = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> None:
        self._source = source
        self.org, self.project = self._parse_source(source)
        self.api_base = (api_base or os.environ.get("AGENT_INDEX_ADO_BASE") or _DEFAULT_API_BASE)
        self.api_base = self.api_base.rstrip("/")
        self._token = token or os.environ.get("AGENT_INDEX_ADO_TOKEN")
        if not self._token:
            raise ValueError("Azure DevOps source requires AGENT_INDEX_ADO_TOKEN or token=...")
        needs_config = work_item_queries is None or (
            pull_request_queries is None
            and pull_request_status is None
            and repository_id is None
        )
        query_config = _load_query_config(config_path) if needs_config else {}
        self._work_item_queries = _resolve_work_item_queries(work_item_queries, query_config)
        self._pull_request_queries = _resolve_pull_request_queries(
            pull_request_queries,
            query_config,
            pull_request_status=pull_request_status,
            repository_id=repository_id,
        )
        self._batch_size = batch_size
        self._page_size = page_size
        self._overlap_seconds = int(
            os.environ.get("AGENT_INDEX_ADO_OVERLAP_SECONDS", str(_DEFAULT_OVERLAP_SECONDS))
        )
        self._repository_ids_by_name: dict[str, str] = {}
        self._authenticated_user_id: str | None = None
        self._logged_no_work_item_queries = False
        self._logged_no_pull_request_queries = False
        self._http = GoodCitizenSession(
            base_url=self.api_base,
            headers=self._headers(),
            transport=transport,
            client=client,
            timeout=_DEFAULT_TIMEOUT,
            min_interval_s=min_interval_s,
        )

    @property
    def source_name(self) -> str:
        """Canonical source family name for this Azure DevOps project."""
        return f"ado:{self.org}/{self.project}"

    def discover(self, cancel_check: Callable[[], None] | None = None) -> list[FileEntry]:
        """Discover work items and pull requests from operator-supplied query specs."""
        return [
            *self._discover_work_items(since=None, cancel_check=cancel_check),
            *self._discover_pull_requests(since=None, cancel_check=cancel_check),
        ]

    def discover_changed(
        self,
        last_commit: str | None,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[FileEntry]:
        """Discover work items and pull requests changed since a timestamp marker."""
        if not last_commit:
            return self.discover(cancel_check=cancel_check)
        since = self._marker_with_overlap(last_commit)
        return [
            *self._discover_work_items(since=since, cancel_check=cancel_check),
            *self._discover_pull_requests(since=since, cancel_check=cancel_check),
        ]

    def list_paths(
        self,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict[str, set[str]]:
        """Return current work item and pull request paths without fetching bodies/comments."""
        work_items: set[str] = set()
        for work_item_id in self._work_item_ids():
            if cancel_check:
                cancel_check()
            work_items.add(f"workitems/{work_item_id}.md")

        pulls: set[str] = set()
        for pull_request in self._iter_pull_requests(since=None):
            if cancel_check:
                cancel_check()
            pull_request_id = pull_request.get("pullRequestId")
            if pull_request_id is not None:
                pulls.add(f"pulls/{pull_request_id}.md")

        return {
            self._workitems_source: work_items,
            self._pulls_source: pulls,
        }

    def current_commit(self) -> str | None:
        """Azure DevOps work tracking state is timestamp-tracked, not git-SHA tracked."""
        return None

    @property
    def _workitems_source(self) -> str:
        return f"{self.source_name}:workitems"

    @property
    def _pulls_source(self) -> str:
        return f"{self.source_name}:pulls"

    def _discover_work_items(
        self,
        *,
        since: str | None,
        cancel_check: Callable[[], None] | None,
    ) -> list[FileEntry]:
        ids = self._work_item_ids()
        entries: list[FileEntry] = []
        since_dt = _parse_datetime(since) if since else None
        for chunk in _chunks(ids, self._batch_size):
            if cancel_check:
                cancel_check()
            result = self._http.post_json(
                self._project_path("_apis", "wit", "workitemsbatch"),
                params={"api-version": _DEFAULT_API_VERSION},
                json={"ids": chunk, "$expand": "all"},
            )
            for item in _values(result.data):
                if isinstance(item, dict):
                    if since_dt and not self._work_item_in_window(item, since_dt):
                        continue
                    comments = self._work_item_comments(item.get("id"))
                    entries.append(self._work_item_entry(item, comments))
        return entries

    def _work_item_ids(self) -> list[int]:
        if not self._work_item_queries:
            self._log_no_work_item_queries()
            return []
        ids: list[int] = []
        for query in self._work_item_queries:
            result = self._run_work_item_query(query)
            ids.extend(_work_item_ids_from_result(result.data))
        return list(dict.fromkeys(ids))

    def _run_work_item_query(self, query: dict[str, Any]) -> Any:
        if wiql := query.get("wiql"):
            result = self._http.post_json(
                self._project_path("_apis", "wit", "wiql"),
                params={"api-version": _DEFAULT_API_VERSION},
                json={"query": wiql},
            )
            return result

        saved_query_id = str(query["saved_query_id"])
        return self._http.get_json(
            self._project_path("_apis", "wit", "wiql", saved_query_id),
            params={"api-version": _DEFAULT_API_VERSION},
        )

    def _work_item_comments(self, work_item_id: Any) -> list[dict[str, Any]]:
        if work_item_id is None:
            return []
        try:
            result = self._http.get_json(
                self._project_path("_apis", "wit", "workItems", str(work_item_id), "comments"),
                params={"api-version": _DEFAULT_COMMENT_API_VERSION},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == httpx.codes.NOT_FOUND:
                return []
            raise
        if isinstance(result.data, dict):
            comments = result.data.get("comments") or result.data.get("value") or []
            return [comment for comment in comments if isinstance(comment, dict)]
        return []

    def _discover_pull_requests(
        self,
        *,
        since: str | None,
        cancel_check: Callable[[], None] | None,
    ) -> list[FileEntry]:
        entries: list[FileEntry] = []
        since_dt = _parse_datetime(since) if since else None
        seen_ids: set[Any] = set()
        for pull_request in self._iter_pull_requests(since=since):
            if cancel_check:
                cancel_check()
            pull_request_id = pull_request.get("pullRequestId")
            if pull_request_id in seen_ids:
                continue
            seen_ids.add(pull_request_id)
            if since_dt and not self._pull_request_in_window(pull_request, since_dt):
                continue
            comments = self._pull_request_comments(pull_request)
            entries.append(self._pull_request_entry(pull_request, comments))
        return entries

    def _iter_pull_requests(self, *, since: str | None) -> Iterable[dict[str, Any]]:
        if not self._pull_request_queries:
            self._log_no_pull_request_queries()
            return

        seen_ids: set[Any] = set()
        for query in self._pull_request_queries:
            params = self._pull_request_params(query)
            if since:
                params["searchCriteria.minTime"] = since

            for result in self._http.paginate_continuation(
                self._project_path("_apis", "git", "pullrequests"),
                params=params,
            ):
                for item in _values(result.data):
                    if not isinstance(item, dict):
                        continue
                    pull_request_id = item.get("pullRequestId")
                    if pull_request_id in seen_ids:
                        continue
                    seen_ids.add(pull_request_id)
                    yield item

    def _pull_request_params(self, query: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "api-version": _DEFAULT_API_VERSION,
            "searchCriteria.status": str(query.get("status") or "all").lower(),
            "$top": self._page_size,
        }
        repository_id = query.get("repository_id")
        if not repository_id and query.get("repository"):
            repository_id = self._resolve_repository_id(str(query["repository"]))
        if repository_id:
            params["searchCriteria.repositoryId"] = str(repository_id)
        if creator := query.get("creator"):
            params["searchCriteria.creatorId"] = self._resolve_identity_filter(creator)
        if reviewer := query.get("reviewer"):
            params["searchCriteria.reviewerId"] = self._resolve_identity_filter(reviewer)
        if source_ref := query.get("source_ref"):
            params["searchCriteria.sourceRefName"] = str(source_ref)
        if target_ref := query.get("target_ref"):
            params["searchCriteria.targetRefName"] = str(target_ref)
        return params

    def _resolve_repository_id(self, repository: str) -> str:
        if _looks_like_guid(repository):
            return repository
        cache_key = repository.casefold()
        if cached := self._repository_ids_by_name.get(cache_key):
            return cached
        result = self._http.get_json(
            self._project_path("_apis", "git", "repositories"),
            params={"api-version": _DEFAULT_API_VERSION},
        )
        for item in _values(result.data):
            if not isinstance(item, dict):
                continue
            repository_id = item.get("id")
            name = item.get("name")
            if repository_id and (
                str(repository_id).casefold() == cache_key
                or (name is not None and str(name).casefold() == cache_key)
            ):
                resolved = str(repository_id)
                self._repository_ids_by_name[cache_key] = resolved
                return resolved
        raise ValueError(f"Azure DevOps repository {repository!r} was not found")

    def _resolve_identity_filter(self, value: Any) -> str:
        text = str(value)
        if text.casefold() == "me":
            return self._get_authenticated_user_id()
        return text

    def _get_authenticated_user_id(self) -> str:
        if self._authenticated_user_id:
            return self._authenticated_user_id
        result = self._http.get_json(
            self._org_path("_apis", "connectionData"),
            params={"api-version": _DEFAULT_API_VERSION},
        )
        user = result.data.get("authenticatedUser") if isinstance(result.data, dict) else None
        user_id = user.get("id") if isinstance(user, dict) else None
        if not user_id:
            raise ValueError("Azure DevOps connectionData did not include authenticatedUser.id")
        self._authenticated_user_id = str(user_id)
        return self._authenticated_user_id

    def _pull_request_comments(self, pull_request: dict[str, Any]) -> list[dict[str, Any]]:
        repository = pull_request.get("repository") or {}
        repository_id = repository.get("id")
        pull_request_id = pull_request.get("pullRequestId")
        if not repository_id or pull_request_id is None:
            return []
        try:
            result = self._http.get_json(
                self._project_path(
                    "_apis",
                    "git",
                    "repositories",
                    str(repository_id),
                    "pullRequests",
                    str(pull_request_id),
                    "threads",
                ),
                params={"api-version": _DEFAULT_API_VERSION},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == httpx.codes.NOT_FOUND:
                return []
            raise
        comments: list[dict[str, Any]] = []
        for thread in _values(result.data):
            if not isinstance(thread, dict):
                continue
            for comment in thread.get("comments", []):
                if isinstance(comment, dict):
                    comments.append(comment)
        return comments

    def _work_item_entry(
        self,
        item: dict[str, Any],
        comments: list[dict[str, Any]],
    ) -> FileEntry:
        fields = item.get("fields") or {}
        work_item_id = item.get("id")
        return FileEntry(
            path=f"workitems/{work_item_id}.md",
            content=self._format_work_item(work_item_id, fields, comments),
            language="work_item",
            source=self._workitems_source,
            metadata={
                "id": work_item_id,
                "work_item_type": fields.get("System.WorkItemType"),
                "state": fields.get("System.State"),
                "area_path": fields.get("System.AreaPath"),
                "iteration_path": fields.get("System.IterationPath"),
                "tags": _tags(fields.get("System.Tags")),
                "assigned_to": _identity_name(fields.get("System.AssignedTo")),
                "changed_date": fields.get("System.ChangedDate"),
            },
        )

    def _pull_request_entry(
        self,
        pull_request: dict[str, Any],
        comments: list[dict[str, Any]],
    ) -> FileEntry:
        pull_request_id = pull_request.get("pullRequestId")
        repository = pull_request.get("repository") or {}
        return FileEntry(
            path=f"pulls/{pull_request_id}.md",
            content=self._format_pull_request(pull_request, comments),
            language="pull_request",
            source=self._pulls_source,
            metadata={
                "id": pull_request_id,
                "status": pull_request.get("status"),
                "repository": repository.get("name"),
                "source_ref": pull_request.get("sourceRefName"),
                "target_ref": pull_request.get("targetRefName"),
                "created_by": _identity_name(pull_request.get("createdBy")),
                "closed_date": pull_request.get("closedDate"),
            },
        )

    def _format_work_item(
        self,
        work_item_id: Any,
        fields: dict[str, Any],
        comments: list[dict[str, Any]],
    ) -> str:
        title = fields.get("System.Title") or "(untitled)"
        lines = [
            f"# {_plain(title)}",
            "",
            f"Type: {fields.get('System.WorkItemType')}",
            f"ID: {work_item_id}",
            f"State: {fields.get('System.State')}",
            f"Area: {fields.get('System.AreaPath')}",
            f"Iteration: {fields.get('System.IterationPath')}",
            f"Assigned To: {_identity_name(fields.get('System.AssignedTo'))}",
            f"Tags: {', '.join(_tags(fields.get('System.Tags')))}",
            f"Changed: {fields.get('System.ChangedDate')}",
        ]
        sections = [
            ("Description", fields.get("System.Description")),
            ("Repro Steps", fields.get("Microsoft.VSTS.TCM.ReproSteps")),
            ("Acceptance Criteria", fields.get("Microsoft.VSTS.Common.AcceptanceCriteria")),
        ]
        for heading, value in sections:
            text = _plain(value)
            if text:
                lines.extend(["", f"## {heading}", "", text])
        if comments:
            lines.extend(["", "## Comments", ""])
            for comment in comments:
                author = _identity_name(comment.get("createdBy") or comment.get("modifiedBy"))
                updated = comment.get("modifiedDate") or comment.get("createdDate")
                text = _plain(comment.get("text") or comment.get("content"))
                if text:
                    lines.extend([f"### {author or 'unknown'} at {updated}", "", text, ""])
        return "\n".join(lines).strip() + "\n"

    def _format_pull_request(
        self,
        pull_request: dict[str, Any],
        comments: list[dict[str, Any]],
    ) -> str:
        repository = pull_request.get("repository") or {}
        lines = [
            f"# {_plain(pull_request.get('title') or '(untitled)')}",
            "",
            "Type: pull_request",
            f"ID: {pull_request.get('pullRequestId')}",
            f"Status: {pull_request.get('status')}",
            f"Repository: {repository.get('name')}",
            f"Source: {pull_request.get('sourceRefName')}",
            f"Target: {pull_request.get('targetRefName')}",
            f"Created By: {_identity_name(pull_request.get('createdBy'))}",
            f"Closed: {pull_request.get('closedDate')}",
            "",
            "## Description",
            "",
            _plain(pull_request.get("description")),
        ]
        if comments:
            lines.extend(["", "## Thread Comments", ""])
            for comment in comments:
                author = _identity_name(comment.get("author"))
                updated = comment.get("lastUpdatedDate") or comment.get("publishedDate")
                text = _plain(comment.get("content"))
                if text:
                    lines.extend([f"### {author or 'unknown'} at {updated}", "", text, ""])
        return "\n".join(lines).strip() + "\n"

    def _marker_with_overlap(self, marker: str) -> str:
        dt = _parse_datetime(marker)
        if dt is None:
            return self._format_marker(marker)
        overlapped = dt - timedelta(seconds=self._overlap_seconds)
        return overlapped.isoformat(timespec="seconds").replace("+00:00", "Z")

    def _format_marker(self, marker: str) -> str:
        dt = _parse_datetime(marker)
        if dt is None:
            return marker
        return dt.isoformat(timespec="seconds").replace("+00:00", "Z")

    def _project_path(self, *parts: str) -> str:
        quoted = [quote(self.org, safe=""), quote(self.project, safe="")]
        quoted.extend(quote(part, safe="") for part in parts)
        return "/" + "/".join(quoted)

    def _org_path(self, *parts: str) -> str:
        quoted = [quote(self.org, safe="")]
        quoted.extend(quote(part, safe="") for part in parts)
        return "/" + "/".join(quoted)

    def _log_no_work_item_queries(self) -> None:
        if self._logged_no_work_item_queries:
            return
        self._logged_no_work_item_queries = True
        log.warning(
            "No Azure DevOps work-item queries configured "
            "(set AGENT_INDEX_ADO_CONFIG or work_item_queries) -- skipping work items."
        )

    def _log_no_pull_request_queries(self) -> None:
        if self._logged_no_pull_request_queries:
            return
        self._logged_no_pull_request_queries = True
        log.warning(
            "No Azure DevOps pull-request queries configured "
            "(set AGENT_INDEX_ADO_CONFIG or pull_request_queries) -- skipping pull requests."
        )

    @staticmethod
    def _parse_source(source: str) -> tuple[str, str]:
        prefixes = ("ado:", "azure-devops:")
        prefix = next((candidate for candidate in prefixes if source.startswith(candidate)), None)
        if prefix is None or "/" not in source[len(prefix):]:
            raise ValueError("Azure DevOps source must be 'ado:<org>/<project>'")
        org_project = source[len(prefix):]
        org, project = org_project.split("/", 1)
        if not org or not project:
            raise ValueError("Azure DevOps source must include both org and project")
        return org, project

    def _headers(self) -> dict[str, str]:
        token = base64.b64encode(f":{self._token}".encode()).decode("ascii")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "agent-index",
            "Authorization": f"Basic {token}",
        }

    @staticmethod
    def _pull_request_in_window(pull_request: dict[str, Any], since: datetime) -> bool:
        for field in ("closedDate", "creationDate"):
            value = pull_request.get(field)
            if not value:
                continue
            parsed = _parse_datetime(value)
            if parsed and parsed >= since:
                return True
        return False

    @staticmethod
    def _work_item_in_window(item: dict[str, Any], since: datetime) -> bool:
        fields = item.get("fields") or {}
        parsed = _parse_datetime(fields.get("System.ChangedDate"))
        return bool(parsed and parsed >= since)


def _load_query_config(config_path: str | Path | None) -> dict[str, Any]:
    explicit_path = config_path or os.environ.get(_ADO_CONFIG_ENV)
    path = Path(explicit_path) if explicit_path else install_dir() / "ado.json"
    if not path.exists():
        if explicit_path:
            raise FileNotFoundError(f"Azure DevOps query config not found: {path}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Azure DevOps query config is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Azure DevOps query config must be a JSON object: {path}")
    return data


def _resolve_work_item_queries(
    explicit: list[dict[str, Any]] | None,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if explicit is not None:
        return _normalize_work_item_queries(explicit, "work_item_queries")
    if (configured := _query_specs_from_config(config, "work_item_queries")) is not None:
        return _normalize_work_item_queries(configured, "work_item_queries")
    if wiql := os.environ.get("AGENT_INDEX_ADO_WIQL"):
        return _normalize_work_item_queries(
            [{"name": "env-wiql", "wiql": wiql}],
            "AGENT_INDEX_ADO_WIQL",
        )
    return []


def _resolve_pull_request_queries(
    explicit: list[dict[str, Any]] | None,
    config: dict[str, Any],
    *,
    pull_request_status: str | None,
    repository_id: str | None,
) -> list[dict[str, Any]]:
    if explicit is not None:
        return _normalize_pull_request_queries(explicit, "pull_request_queries")
    if pull_request_status is not None or repository_id is not None:
        query: dict[str, Any] = {"name": "kwargs-pr", "status": pull_request_status or "all"}
        if repository_id is not None:
            query["repository_id"] = repository_id
        return _normalize_pull_request_queries([query], "pull-request kwargs")
    if (configured := _query_specs_from_config(config, "pull_request_queries")) is not None:
        return _normalize_pull_request_queries(configured, "pull_request_queries")
    env_query = _pull_request_query_from_env()
    if not env_query:
        return []
    return _normalize_pull_request_queries([env_query], "AGENT_INDEX_ADO_PR_*")


def _query_specs_from_config(config: dict[str, Any], key: str) -> list[dict[str, Any]] | None:
    if key not in config:
        return None
    value = config[key]
    if not isinstance(value, list):
        raise ValueError(f"Azure DevOps config field {key!r} must be a list")
    specs: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Azure DevOps config field {key}[{index}] must be an object")
        specs.append(dict(item))
    return specs


def _normalize_work_item_queries(
    queries: list[dict[str, Any]],
    source: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, query in enumerate(queries, start=1):
        if not isinstance(query, dict):
            raise ValueError(f"Azure DevOps {source}[{index}] must be an object")
        has_wiql = bool(query.get("wiql"))
        has_saved = bool(query.get("saved_query_id"))
        if has_wiql == has_saved:
            raise ValueError(
                f"Azure DevOps {source}[{index}] must set exactly one of "
                "'wiql' or 'saved_query_id'"
            )
        spec = {"name": str(query.get("name") or f"work-item-query-{index}")}
        if has_wiql:
            spec["wiql"] = str(query["wiql"])
        else:
            spec["saved_query_id"] = str(query["saved_query_id"])
        normalized.append(spec)
    return normalized


def _normalize_pull_request_queries(
    queries: list[dict[str, Any]],
    source: str,
) -> list[dict[str, Any]]:
    allowed = {
        "name",
        "status",
        "repository",
        "repository_id",
        "creator",
        "reviewer",
        "source_ref",
        "target_ref",
    }
    normalized: list[dict[str, Any]] = []
    for index, query in enumerate(queries, start=1):
        if not isinstance(query, dict):
            raise ValueError(f"Azure DevOps {source}[{index}] must be an object")
        spec = {
            key: value
            for key, value in query.items()
            if key in allowed and value is not None and value != ""
        }
        spec["name"] = str(spec.get("name") or f"pull-request-query-{index}")
        spec["status"] = str(spec.get("status") or "all").lower()
        normalized.append(spec)
    return normalized


def _pull_request_query_from_env() -> dict[str, Any] | None:
    env_map = {
        "status": os.environ.get("AGENT_INDEX_ADO_PR_STATUS"),
        "repository": os.environ.get("AGENT_INDEX_ADO_PR_REPOSITORY"),
        "repository_id": os.environ.get("AGENT_INDEX_ADO_REPOSITORY_ID"),
        "creator": os.environ.get("AGENT_INDEX_ADO_PR_CREATOR"),
        "reviewer": os.environ.get("AGENT_INDEX_ADO_PR_REVIEWER"),
    }
    query = {key: value for key, value in env_map.items() if value}
    if not query:
        return None
    query.setdefault("name", "env-pr")
    query.setdefault("status", "all")
    return query


def _work_item_ids_from_result(data: Any) -> list[int]:
    ids: list[int] = []
    for item in data.get("workItems", []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("id") is not None:
            ids.append(int(item["id"]))
    relations = data.get("workItemRelations", []) if isinstance(data, dict) else []
    for relation in relations:
        target = relation.get("target") if isinstance(relation, dict) else None
        if isinstance(target, dict) and target.get("id") is not None:
            ids.append(int(target["id"]))
    return ids


def _values(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        value = data.get("value")
        if isinstance(value, list):
            return value
    return []


def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for index in range(0, len(values), max(size, 1)):
        yield values[index : index + size]


def _tags(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(";") if part.strip()]


def _identity_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("displayName") or value.get("uniqueName") or value.get("id")
    if value is None:
        return None
    return str(value)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except ValueError:
        pass
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _plain(value: Any) -> str:
    if value is None:
        return ""
    stripper = _HtmlStripper()
    stripper.feed(str(value))
    stripper.close()
    stripped = unescape(stripper.text())
    stripped = re.sub(r"[ \t\r\f\v]+", " ", stripped)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    return stripped.strip()


def _looks_like_guid(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            value,
        )
    )


class _HtmlStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "div", "p", "li"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"div", "p", "li"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)
