"""Azure DevOps work items and pull requests source connector."""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import httpx

from agent_index.sources.base import FileEntry
from agent_index.sources.good_citizen_http import GoodCitizenSession

_DEFAULT_API_BASE = "https://dev.azure.com"
_DEFAULT_TIMEOUT = 20.0
_DEFAULT_MIN_INTERVAL_S = 0.2
_DEFAULT_API_VERSION = "7.1"
_DEFAULT_COMMENT_API_VERSION = "7.1-preview.3"
_DEFAULT_BATCH_SIZE = 200
_DEFAULT_PAGE_SIZE = 100
_DEFAULT_LOOKBACK_DAYS = 30
_DEFAULT_OVERLAP_SECONDS = 300


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
        self._changed_since = changed_since or os.environ.get("AGENT_INDEX_ADO_CHANGED_SINCE")
        self._area_path = area_path or os.environ.get("AGENT_INDEX_ADO_AREA_PATH")
        self._iteration_path = iteration_path or os.environ.get("AGENT_INDEX_ADO_ITERATION_PATH")
        self._pull_request_status = (
            pull_request_status or os.environ.get("AGENT_INDEX_ADO_PR_STATUS") or "all"
        ).lower()
        self._repository_id = repository_id or os.environ.get("AGENT_INDEX_ADO_REPOSITORY_ID")
        self._batch_size = batch_size
        self._page_size = page_size
        self._overlap_seconds = int(
            os.environ.get("AGENT_INDEX_ADO_OVERLAP_SECONDS", str(_DEFAULT_OVERLAP_SECONDS))
        )
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
        """Discover work items and pull requests within the configured bounded scope."""
        since = self._bounded_since_marker()
        return [
            *self._discover_work_items(since=since, cancel_check=cancel_check),
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
        since = self._bounded_since_marker()
        work_items: set[str] = set()
        for work_item_id in self._work_item_ids(since=since):
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
        since: str,
        cancel_check: Callable[[], None] | None,
    ) -> list[FileEntry]:
        ids = self._work_item_ids(since=since)
        entries: list[FileEntry] = []
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
                    comments = self._work_item_comments(item.get("id"))
                    entries.append(self._work_item_entry(item, comments))
        return entries

    def _work_item_ids(self, *, since: str) -> list[int]:
        result = self._http.post_json(
            self._project_path("_apis", "wit", "wiql"),
            params={"api-version": _DEFAULT_API_VERSION},
            json={"query": self._wiql(since=since)},
        )
        ids: list[int] = []
        for item in result.data.get("workItems", []) if isinstance(result.data, dict) else []:
            if isinstance(item, dict) and item.get("id") is not None:
                ids.append(int(item["id"]))
        relations = (
            result.data.get("workItemRelations", []) if isinstance(result.data, dict) else []
        )
        for relation in relations:
            target = relation.get("target") if isinstance(relation, dict) else None
            if isinstance(target, dict) and target.get("id") is not None:
                ids.append(int(target["id"]))
        return list(dict.fromkeys(ids))

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
        for pull_request in self._iter_pull_requests(since=since):
            if cancel_check:
                cancel_check()
            if since_dt and not self._pull_request_in_window(pull_request, since_dt):
                continue
            comments = self._pull_request_comments(pull_request)
            entries.append(self._pull_request_entry(pull_request, comments))
        return entries

    def _iter_pull_requests(self, *, since: str | None) -> Iterable[dict[str, Any]]:
        params: dict[str, Any] = {
            "api-version": _DEFAULT_API_VERSION,
            "searchCriteria.status": self._pull_request_status,
            "$top": self._page_size,
        }
        if self._repository_id:
            params["searchCriteria.repositoryId"] = self._repository_id
        if since:
            params["searchCriteria.minTime"] = since

        for result in self._http.paginate_continuation(
            self._project_path("_apis", "git", "pullrequests"),
            params=params,
        ):
            for item in _values(result.data):
                if isinstance(item, dict):
                    yield item

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

    def _wiql(self, *, since: str) -> str:
        clauses = [
            f"[System.TeamProject] = '{_wiql_escape(self.project)}'",
            f"[System.ChangedDate] >= '{_wiql_escape(since)}'",
        ]
        if self._area_path:
            clauses.append(f"[System.AreaPath] UNDER '{_wiql_escape(self._area_path)}'")
        if self._iteration_path:
            clauses.append(f"[System.IterationPath] UNDER '{_wiql_escape(self._iteration_path)}'")
        return (
            "SELECT [System.Id] FROM WorkItems WHERE "  # noqa: S608 - WIQL, not SQL.
            + " AND ".join(clauses)
            + " ORDER BY [System.ChangedDate] ASC"
        )

    def _bounded_since_marker(self) -> str:
        if self._changed_since:
            return self._format_marker(self._changed_since)
        marker = datetime.now(UTC) - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
        return marker.isoformat(timespec="seconds").replace("+00:00", "Z")

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


def _wiql_escape(value: str) -> str:
    return value.replace("'", "''")


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
