"""Gitea metadata updates through the selected provider's existing HTTP owner."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from ..pr_contract import _BUILTIN_WIP_PREFIXES, _SERVER_NATIVE_WIP_PREFIXES, title_is_wip
from .attribution_body import preserve_description, published_marker
from .base import ProviderError, reject_copilot_mention

_FAILURES = (ValueError, OSError, ProviderError, subprocess.SubprocessError)
_REJECTED = {400, 401, 403, 404, 405, 409, 410, 412, 422}


def _title_safe(current: str, requested: str, draft: bool,
                prefixes: tuple[str, ...]) -> None:
    if title_is_wip(current, prefixes) != title_is_wip(requested, prefixes):
        raise ProviderError("Metadata title must preserve the configured WIP hold")
    known = (*prefixes, *_BUILTIN_WIP_PREFIXES)
    original = current.lstrip().casefold()
    desired = requested.lstrip().casefold()
    matched = [p for p in known if p.strip() and original.startswith(p.casefold())]
    if draft and not matched:
        raise ProviderError("Native draft prefix is unrecognized; retain title and edit body only")
    if not draft and any(original.startswith(p) for p in _SERVER_NATIVE_WIP_PREFIXES):
        raise ProviderError("Native draft evidence contradicts the current title")
    if matched:
        prefix = max(matched, key=len)
        if not desired.startswith(prefix.casefold()):
            raise ProviderError("Metadata title must retain the existing draft/WIP prefix")
    elif any(desired.startswith(p.casefold()) for p in known if p.strip()):
        raise ProviderError("Metadata title cannot introduce a draft/WIP prefix")


def update(provider: Any, repo: str, number: int, *,
           api_base: str = "", token: str | None = None,
           title: str | None = None, description: str | None = None,
           reviewers: tuple[str, ...] = (), work_items: tuple[str | int, ...] = (),
           origin: Any = None, wip_title_prefixes: tuple[str, ...] = ()) -> dict[str, Any]:
    """Preserve source and draft state, confirming each selected action separately."""
    actions: dict[str, dict[str, Any]] = {
        key: {"status": "not_attempted"}
        for key, value in (("title", title), ("description", description))
        if value is not None
    }
    result: dict[str, Any] = {
        "schema": "agent-worktrees.pr-metadata", "version": 1,
        "provider": "gitea", "repo": repo, "number": number,
        "url": f"{api_base.rstrip('/')}/{repo}/pulls/{number}",
        "publication_retained": True, "ok": False, "actions": actions,
    }
    try:
        if (
            not isinstance(repo, str) or not re.fullmatch(r"[^/\s?#]+/[^/\s?#]+", repo)
            or type(number) is not int or number < 1
        ):
            raise ValueError("Gitea metadata needs owner/repository and a positive PR number")
        if not token:
            raise ProviderError("Gitea metadata requires the selected target account token")
        if reviewers or work_items or origin is not None:
            raise ValueError("Gitea metadata supports title and description only")
        if not actions:
            raise ValueError("Gitea metadata needs a title or description update")
        if title is not None and (not isinstance(title, str) or not title.strip()):
            raise ValueError("Title must be a nonempty string")
        if description is not None and not isinstance(description, str):
            raise ValueError("Description must be a string")
        for value in (title, description):
            if value is not None:
                reject_copilot_mention(value, what="PR metadata")
        endpoint = provider._api(api_base, f"/repos/{repo}/pulls/{number}")
    except _FAILURES as exc:
        result["error"] = str(exc)
        return result

    def request(method: str, payload: dict[str, str] | None = None) -> tuple[int, str]:
        response: tuple[int, str] = provider._curl(
            method, endpoint, token, payload=payload, preserve_line_endings=True,
            payload_stdin=True,
        )
        return response

    def pull() -> dict[str, Any]:
        status, body = request("GET")
        data = json.loads(body)
        base = data.get("base") if isinstance(data, dict) else None
        repository = base.get("repo") if isinstance(base, dict) else None
        full_name = repository.get("full_name") if isinstance(repository, dict) else None
        if (
            status != 200 or not isinstance(data, dict)
            or type(data.get("number")) is not int or data["number"] != number
            or not isinstance(full_name, str) or full_name.casefold() != repo.casefold()
            or data.get("state") != "open" or data.get("merged") is not False
            or not isinstance(data.get("title"), str)
            or data.get("body") is not None and not isinstance(data["body"], str)
        ):
            raise ProviderError("Gitea metadata needs authoritative same-target active PR evidence")
        return data

    try:
        initial = pull()
        desired: dict[str, str] = {}
        if title is not None:
            if type(initial.get("draft")) is not bool:
                raise ProviderError("Gitea title editing requires native draft evidence")
            _title_safe(initial["title"], title, initial["draft"], wip_title_prefixes)
            desired["title"] = title
        if description is not None:
            desired["description"] = preserve_description(initial.get("body") or "", description)
    except _FAILURES as exc:
        result["error"] = str(exc)
        return result
    for key in actions:
        field = "body" if key == "description" else key
        value = desired[key]
        attempted, rejected = False, False
        try:
            before = pull()
            if key == "title":
                if type(before.get("draft")) is not bool or before["draft"] != initial["draft"]:
                    raise ProviderError("Native draft state changed; reconcile before updating title")
                _title_safe(before["title"], value, before["draft"], wip_title_prefixes)
            elif published_marker(before.get("body") or "") != published_marker(initial.get("body") or ""):
                raise ProviderError("Original source attribution changed; reconcile before updating")
            if before.get(field) == value:
                actions[key] = {"status": "confirmed", "changed": False}
                continue
            attempted = True
            write_error = ""
            try:
                status, _ = request("PATCH", {field: value})
                rejected = status in _REJECTED
                if not 200 <= status < 300:
                    write_error = f"Gitea metadata PATCH returned HTTP {status}"
            except _FAILURES:
                write_error = "Gitea metadata write response is unconfirmed"
            after = pull()
            if after.get(field) != value or (
                key == "title" and (
                    type(after.get("draft")) is not bool or after["draft"] != initial["draft"]
                )
            ):
                raise ProviderError(write_error or "Requested Gitea metadata was not confirmed by readback")
            actions[key] = {"status": "confirmed", "changed": not rejected}
        except _FAILURES as exc:
            actions[key] = {
                "status": ("failed" if rejected else "unknown") if attempted else "not_attempted",
                "error": str(exc),
            }
            result["error"] = str(exc)
            return result
    result["ok"] = True
    return result
