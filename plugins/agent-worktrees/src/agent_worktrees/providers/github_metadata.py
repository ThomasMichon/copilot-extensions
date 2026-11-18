"""GitHub title/body updates with source preservation and authoritative readback."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Callable

from .attribution_body import preserve_description, published_marker
from .base import ProviderError, reject_copilot_mention

SCHEMA = "agent-worktrees.pr-metadata"
_FAILURES = (ValueError, OSError, ProviderError, subprocess.SubprocessError)
_REJECTED = {400, 401, 403, 404, 405, 409, 410, 412, 422}


def _response(text: str) -> tuple[int, dict[str, Any]]:
    headers, separator, body = text.replace("\r\n", "\n").partition("\n\n")
    match = re.match(r"HTTP/\S+ ([1-5][0-9]{2})(?:[ \r\n]|$)", headers)
    if not separator or match is None:
        raise ValueError("GitHub metadata response has no authoritative HTTP status")
    status = int(match[1])
    data = json.loads(body) if 200 <= status < 300 else {}
    if not isinstance(data, dict):
        raise ValueError("GitHub metadata response is not a PR object")
    return status, data


def update(
    provider: Any,
    repo: str,
    number: int,
    *,
    api_base: str = "",
    token: str | None = None,
    title: str | None = None,
    description: str | None = None,
    reviewers: tuple = (),
    work_items: tuple = (),
    origin: Any = None,
    run: Callable[..., Any],
) -> dict[str, Any]:
    """Update selected existing fields, never replaying an uncertain dispatch."""
    host = provider.authority_endpoint(api_base)
    actions = {
        key: {"status": "not_attempted"}
        for key, value in (("title", title), ("description", description))
        if value is not None
    }
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "version": 1,
        "provider": provider.name,
        "repo": repo,
        "number": number,
        "url": f"https://{host}/{repo}/pull/{number}",
        "publication_retained": True,
        "ok": False,
        "actions": actions,
    }
    try:
        if (
            not isinstance(repo, str)
            or not re.fullmatch(r"[^/\s?#]+/[^/\s?#]+", repo)
            or type(number) is not int
            or number < 1
        ):
            raise ValueError("GitHub metadata needs owner/repository and a positive PR number")
        if reviewers or work_items or origin is not None:
            raise ValueError("GitHub metadata supports title and description only")
        if not actions:
            raise ValueError("GitHub metadata needs a title or description update")
        if title is not None and (not isinstance(title, str) or not title.strip()):
            raise ValueError("Title must be a nonempty string")
        if description is not None and not isinstance(description, str):
            raise ValueError("Description must be a string")
        for value in (title, description):
            if value is not None:
                reject_copilot_mention(value, what="PR metadata")
    except _FAILURES as exc:
        result["error"] = str(exc)
        return result
    endpoint = f"repos/{repo}/pulls/{number}"
    environment = provider._env(token, host=host)

    def request(method: str, payload: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        args = ["gh", "api", "--hostname", host, "--include", "-X", method, endpoint]
        kwargs: dict[str, Any] = {"env": environment}
        if payload is not None:
            args.extend(["--input", "-"])
            kwargs["input_text"] = json.dumps(payload, ensure_ascii=False)
        proc = run(args, **kwargs)
        status, data = _response(proc.stdout)
        if proc.returncode and 200 <= status < 300:
            raise ProviderError("GitHub metadata transport did not confirm its response")
        return status, data

    def pull() -> dict[str, Any]:
        status, data = request("GET")
        base = data.get("base")
        repository = base.get("repo") if isinstance(base, dict) else None
        full_name = repository.get("full_name") if isinstance(repository, dict) else None
        if (
            status != 200
            or type(data.get("number")) is not int
            or data["number"] != number
            or not isinstance(full_name, str)
            or full_name.casefold() != repo.casefold()
            or data.get("state") != "open"
            or data.get("merged") is not False
            or type(data.get("draft")) is not bool
            or not isinstance(data.get("title"), str)
            or data.get("body") is not None
            and not isinstance(data["body"], str)
        ):
            raise ProviderError(
                "GitHub metadata needs authoritative same-target active PR evidence"
            )
        return data

    try:
        initial = pull()
        desired = {"title": title}
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
            if key == "description" and published_marker(
                before.get("body") or ""
            ) != published_marker(initial.get("body") or ""):
                raise ProviderError(
                    "Original source attribution changed; reconcile before updating"
                )
            if before.get(field) == value:
                actions[key] = {"status": "confirmed", "changed": False}
                continue
            attempted = True
            write_error = ""
            try:
                status, _ = request("PATCH", {field: value})
                rejected = status in _REJECTED
                if not 200 <= status < 300:
                    write_error = f"GitHub metadata PATCH returned HTTP {status}"
            except _FAILURES:
                write_error = "GitHub metadata write response is unconfirmed"
            after = pull()
            if after.get(field) != value:
                raise ProviderError(
                    write_error or "Requested GitHub metadata was not confirmed by readback"
                )
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
