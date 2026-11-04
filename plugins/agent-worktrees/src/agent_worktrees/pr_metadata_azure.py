"""Verified, additive Azure DevOps author metadata using the provider transport."""
from __future__ import annotations

import json
from urllib.parse import quote
from uuid import UUID

from .providers.base import ProviderError


def _guid(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("ADO identities must be GUID strings")
    return str(UUID(value))


def _work_items(values):
    items = {}
    for value in values:
        project = ""
        if isinstance(value, str) and "/" in value:
            project, raw_id = value.rsplit("/", 1)
            if not project.strip() or project != project.strip() or not raw_id.isascii() or not raw_id.isdecimal():
                raise ValueError("Work items must be project/id or positive integer IDs")
            value = int(raw_id)
        if type(value) is not int or value < 1:
            raise ValueError("Work items must be project/id or positive integer IDs")
        if value in items and items[value] and project and items[value].casefold() != project.casefold():
            raise ValueError("Conflicting project bindings for one work item")
        items[value] = project or items.get(value, "")
    return items


def update(provider, repo: str, number: int, *, api_base: str = "", token=None,
           title: str | None = None, description: str | None = None,
           reviewers=(), work_items=(), origin=None) -> dict:
    """Converge requested fields, retaining identity and uncertain effects.

    Each action reads before writing and verifies afterward, including after a
    transport failure. A subsequent call reconciles the same target rather than
    blindly replaying a write. No action removes reviewers, labels or relations.
    """
    from . import pr_publication_labels

    if not api_base or type(number) is not int or number < 1:
        raise ValueError("ADO metadata needs an organization and positive PR number")
    project, name = provider._split_repo(repo)
    if not project or not name:
        raise ValueError("ADO metadata needs project/repository")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise ValueError("Title must be a nonempty string")
    if description is not None and not isinstance(description, str):
        raise ValueError("Description must be a string")
    reviewer_ids = tuple(dict.fromkeys(_guid(value) for value in reviewers))
    items = _work_items(work_items)
    actions = {}
    if title is not None:
        actions["title"] = {"status": "not_attempted"}
    if description is not None:
        actions["description"] = {"status": "not_attempted"}
    for reviewer in reviewer_ids:
        actions[f"reviewer:{reviewer}"] = {"status": "not_attempted"}
    for item in items:
        actions[f"work_item:{item}"] = {"status": "not_attempted"}
    if origin is not None:
        actions["publication_labels"] = {"status": "not_attempted"}
    result = {
        "ok": False, "provider": provider.name, "api_base": api_base.rstrip("/"),
        "repo": repo, "number": number,
        "url": provider._web_url(api_base, quote(project, safe=""), quote(name, safe=""), number),
        "publication_retained": True, "actions": actions,
    }
    base = (f"{api_base.rstrip('/')}/{quote(project, safe='')}/_apis/git/repositories/"
            f"{quote(name, safe='')}/pullRequests/{number}")
    auth, error = provider._auth_header(token)
    if error:
        result["error"] = error
        return result

    def request(method, url, payload=None, *, patch=False):
        kwargs = {"content_type": "application/json-patch+json"} if patch else {}
        status, body = provider._rest_call(
            method, url, auth,
            json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            **kwargs,
        )
        if status not in (200, 201):
            raise ProviderError(f"ADO metadata {method} returned HTTP {status}")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ProviderError("Malformed ADO metadata response")
        return data

    def pull():
        data = request("GET", base + "?api-version=7.1")
        repository = data.get("repository") or {}
        if not isinstance(repository, dict):
            raise ProviderError("Malformed ADO repository identity")
        actual_project = repository.get("project") or {}
        if not isinstance(actual_project, dict):
            raise ProviderError("Malformed ADO repository identity")
        if (data.get("pullRequestId") != number
                or not any(str(repository.get(key, "")).casefold() == name.casefold()
                           for key in ("id", "name"))
                or not any(str(actual_project.get(key, "")).casefold() == project.casefold()
                           for key in ("id", "name"))):
            raise ProviderError("ADO metadata readback differs from the requested PR identity")
        return data

    failures = (ProviderError, OSError, ValueError, TypeError, KeyError)

    def perform(key, read, satisfied, write):
        try:
            before = read()
            if satisfied(before):
                actions[key] = {"status": "confirmed", "changed": False}
                return True
            write_error = ""
            try:
                write(before)
            except failures as exc:
                write_error = str(exc)
            if satisfied(read()):
                actions[key] = {"status": "confirmed", "changed": True}
                return True
            raise ProviderError(write_error or "Requested metadata was not confirmed by readback")
        except failures as exc:
            actions[key] = {"status": "unknown", "error": str(exc)}
            return False

    try:
        initial = pull()
    except failures as exc:
        result["error"] = str(exc)
        return result
    for field, value in (("title", title), ("description", description)):
        if value is not None and not perform(
                field, pull, lambda data, f=field, v=value: data.get(f) == v,
                lambda _, f=field, v=value: request("PATCH", base + "?api-version=7.1", {f: v})):
            return result
    for reviewer in reviewer_ids:
        def has_reviewer(data):
            current = data.get("reviewers")
            if not isinstance(current, list) or any(not isinstance(value, dict) for value in current):
                raise ProviderError("Malformed ADO reviewer readback")
            return any(str(value.get("id", "")).casefold() == reviewer for value in current)

        if not perform(
                f"reviewer:{reviewer}", pull, has_reviewer,
                lambda _: request("PUT", base + f"/reviewers/{reviewer}?api-version=7.1",
                                  {"id": reviewer})):
            return result
    for item in items:
        try:
            repository = initial["repository"]
            project_id = _guid(repository["project"]["id"])
            repo_id = _guid(repository["id"])
            artifact = f"vstfs:///Git/PullRequestId/{project_id}%2F{repo_id}%2F{number}"
            wi_url = f"{api_base.rstrip('/')}/_apis/wit/workitems/{item}"

            def read_link():
                wi = request("GET", wi_url + "?$expand=relations&api-version=7.1")
                if wi.get("id") != item:
                    raise ProviderError("Work-item readback differs from requested ID")
                if items[item]:
                    fields = wi.get("fields")
                    if (not isinstance(fields, dict)
                            or str(fields.get("System.TeamProject", "")).casefold() != items[item].casefold()):
                        raise ProviderError("Work item does not belong to the requested project")
                links = request("GET", base + "/workitems?api-version=7.1").get("value")
                if not isinstance(links, list) or any(not isinstance(value, dict) for value in links):
                    raise ProviderError("Malformed PR work-item readback")
                return wi, links

            def linked(wi):
                relations = wi.get("relations", [])
                if not isinstance(relations, list) or any(not isinstance(value, dict) for value in relations):
                    raise ProviderError("Malformed work-item relations")
                return any(value.get("rel") == "ArtifactLink" and value.get("url") == artifact
                           for value in relations)

            def satisfied_link(data):
                wi, links = data
                return linked(wi) and any(str(value.get("id")) == str(item) for value in links)

            def write_link(data):
                wi, _ = data
                if linked(wi):
                    return
                revision = wi.get("rev")
                if type(revision) is not int or revision < 1:
                    raise ProviderError("Work-item update requires an observed revision")
                request("PATCH", wi_url + "?api-version=7.1", [
                    {"op": "test", "path": "/rev", "value": revision},
                    {"op": "add", "path": "/relations/-", "value": {
                        "rel": "ArtifactLink", "url": artifact,
                        "attributes": {"name": "Pull Request"},
                    }},
                ], patch=True)

            if not perform(f"work_item:{item}", read_link, satisfied_link, write_link):
                return result
        except failures as exc:
            actions[f"work_item:{item}"] = {"status": "unknown", "error": str(exc)}
            return result
    if origin is not None:
        labels = pr_publication_labels.reconcile(
            provider, repo, number, origin, api_base=api_base, token=token,
        )
        if labels and not labels["ok"]:
            # Reconcile a lost write response by reading, never replaying it here.
            try:
                after = provider.get_publication_labels(repo, number, api_base=api_base, token=token)
                labels["ok"] = {label.casefold() for label in origin.labels}.issubset(
                    label.casefold() for label in after)
            except failures:
                pass
        actions["publication_labels"] = {
            "status": "confirmed" if labels and labels["ok"] else "unknown",
            "result": labels,
        }
        if not labels or not labels["ok"]:
            return result
    result["ok"] = True
    return result
