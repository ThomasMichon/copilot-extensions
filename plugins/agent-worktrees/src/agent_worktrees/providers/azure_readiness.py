"""Draft-only ADO readiness using the existing provider transport."""
from __future__ import annotations

import json
import subprocess
from typing import Any
from urllib.parse import quote

from ..pr_readiness import outcome
from .base import ProviderError


def ready(provider: Any, repo: str, number: int, *, api_base: str = "",
          token: str | None = None) -> dict[str, Any]:
    """Refuse incomplete preflight; preserve uncertainty after the single write."""
    def result(status: str, error: str = "") -> dict[str, Any]:
        return outcome(repo, number, provider=provider.name, status=status, error=error)

    failures = (RuntimeError, OSError, ValueError, TypeError, subprocess.SubprocessError)
    try:
        if not api_base or type(number) is not int or number < 1:
            raise ValueError("ADO readiness needs an organization and positive PR number")
        project, repository = provider._split_repo(repo)
        if not project or not repository:
            raise ValueError("ADO readiness needs project/repository")
        auth, error = provider._auth_header(token)
        if error:
            return result("not-attempted", error)
        url = (
            f"{api_base.rstrip('/')}/{quote(project, safe='')}/_apis/git/repositories/"
            f"{quote(repository, safe='')}/pullRequests/{number}?api-version=7.1"
        )

        def validate(data: Any) -> dict[str, Any]:
            if not isinstance(data, dict):
                raise ProviderError("Malformed ADO readiness evidence")
            native_repo = data.get("repository")
            native_project = native_repo.get("project") if isinstance(native_repo, dict) else None
            if (
                type(data.get("pullRequestId")) is not int or data["pullRequestId"] != number
                or not isinstance(native_repo, dict) or not isinstance(native_project, dict)
                or not any(isinstance(native_repo.get(key), str)
                           and native_repo[key].casefold() == repository.casefold() for key in ("id", "name"))
                or not any(isinstance(native_project.get(key), str)
                           and native_project[key].casefold() == project.casefold() for key in ("id", "name"))
            ):
                raise ProviderError("ADO readiness evidence differs from the selected PR identity")
            if data.get("status") != "active" or type(data.get("isDraft")) is not bool:
                raise ProviderError("ADO readiness requires confirmed active PR and native draft evidence")
            return data

        def read() -> dict[str, Any]:
            code, body = provider._rest_call("GET", url, auth)
            if code != 200:
                raise ProviderError(f"ADO readiness read returned HTTP {code}")
            return validate(json.loads(body))

        if not read()["isDraft"]:
            return result("not-attempted", "PR is already ready; no draft transition was attempted")
    except failures as exc:
        return result("not-attempted", str(exc))

    try:
        code, body = provider._rest_call("PATCH", url, auth, json.dumps({"isDraft": False}))
        if code in (400, 401, 403, 404, 405, 409, 412, 422):
            return result("failed", f"ADO readiness write was rejected (HTTP {code}); inspect this PR")
        if code != 200:
            raise ProviderError(f"ADO readiness write is unconfirmed (HTTP {code})")
        validate(json.loads(body))
        after = read()
        if after["isDraft"]:
            return result("failed", "PR remains draft after readiness write; inspect this PR before retrying")
        return result("confirmed")
    except failures as exc:
        return result("unknown", f"{exc}; reconcile this PR before retrying readiness")
