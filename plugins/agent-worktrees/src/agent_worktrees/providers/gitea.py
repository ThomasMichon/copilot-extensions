"""Gitea PR provider -- ``curl`` against the Gitea REST API.

Gitea has no installed CLI (``tea`` is absent on the deploy machines), so this
provider shells out to ``curl`` -- still "a CLI, not a Python HTTP library",
honoring the no-new-dependency constraint.  A token is required (resolved by
``base.resolve_token`` from ``pr.token_command`` / ``pr.token_env``).
"""

from __future__ import annotations

import json

from .base import ProviderError, PRScope, PullResult, run_cli


class GiteaProvider:
    """Open + query pull requests on a Gitea instance via curl."""

    name = "gitea"

    def _api(self, api_base: str, path: str) -> str:
        base = (api_base or "").rstrip("/")
        if not base:
            raise ProviderError(
                "Gitea provider needs 'pr.api_base' (e.g. "
                "https://host/gitea) to know which instance to call."
            )
        return f"{base}/api/v1{path}"

    def _curl(
        self,
        method: str,
        url: str,
        token: str,
        *,
        payload: dict | None = None,
    ) -> tuple[int, str]:
        """Run curl; return (http_status, body_text)."""
        args = [
            "curl", "-sS", "-X", method, url,
            "-H", f"Authorization: token {token}",
            "-H", "Accept: application/json",
            "-w", "\n%{http_code}",
        ]
        if payload is not None:
            args += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
        proc = run_cli(args)
        if proc.returncode != 0:
            raise ProviderError(
                f"curl failed talking to Gitea ({url}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        out = proc.stdout
        nl = out.rfind("\n")
        body, status_str = (out[:nl], out[nl + 1:]) if nl >= 0 else ("", out)
        try:
            status = int(status_str.strip())
        except ValueError:
            status = 0
        return status, body

    def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
        if not token:
            raise ProviderError(
                "Gitea provider needs a token. Set 'pr.token_command' (e.g. a "
                "vault fetch) or 'pr.token_env' in the repo config."
            )
        url = self._api(scope.api_base, f"/repos/{scope.repo}/pulls")
        status, body = self._curl(
            "POST", url, token,
            payload={
                "head": scope.head,
                "base": scope.base,
                "title": scope.title,
                "body": scope.body,
            },
        )
        if status not in (200, 201):
            raise ProviderError(
                f"Gitea PR creation failed (HTTP {status}) for "
                f"{scope.repo} {scope.head}->{scope.base}: {body.strip()[:300]}"
            )
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise ProviderError(f"Gitea returned non-JSON PR response: {e}") from e
        result = PullResult(
            url=str(data.get("html_url", "")),
            number=int(data["number"]) if data.get("number") is not None else None,
            state=str(data.get("state", "open")) or "open",
        )
        if scope.labels and result.number is not None:
            self._apply_labels(scope, result.number, token)
        return result

    def _all_labels(self, scope: PRScope, token: str) -> dict[str, int]:
        """Resolve ``label-name (lowercased) -> id`` for the repo, **paginated**.

        Gitea's ``GET /repos/{repo}/labels`` returns a single page (default 30),
        so a repo with more labels than fit on page 1 leaves later labels
        invisible. A previous single-page fetch here silently dropped any
        configured label past that boundary (e.g. a freshly-created
        ``source:<machine>``). We page with an explicit ``limit`` until a short
        page, so every label resolves. Returns ``{}`` on the first non-200.
        """
        by_name: dict[str, int] = {}
        page = 1
        page_size = 50
        while True:
            status, body = self._curl(
                "GET",
                self._api(
                    scope.api_base,
                    f"/repos/{scope.repo}/labels?limit={page_size}&page={page}",
                ),
                token,
            )
            if status != 200:
                return {} if page == 1 else by_name
            batch = json.loads(body)
            if not isinstance(batch, list):
                break
            for lbl in batch:
                if isinstance(lbl, dict) and lbl.get("name") and lbl.get("id") is not None:
                    by_name[str(lbl["name"]).lower()] = lbl["id"]
            if len(batch) < page_size:
                break
            page += 1
        return by_name

    def _apply_labels(self, scope: PRScope, number: int, token: str) -> None:
        """Best-effort: resolve label names to ids and attach them.

        Gitea's issue-label endpoint takes label **ids**, so names are mapped
        via the repo label list first.  Failures are swallowed -- a missing
        label must not fail an otherwise-created PR (it is reported by the
        caller's verification step).
        """
        try:
            by_name = self._all_labels(scope, token)
            ids = [by_name[name.lower()] for name in scope.labels
                   if name.lower() in by_name and by_name[name.lower()] is not None]
            if not ids:
                return
            self._curl(
                "POST",
                self._api(scope.api_base, f"/repos/{scope.repo}/issues/{number}/labels"),
                token,
                payload={"labels": ids},
            )
        except (ProviderError, json.JSONDecodeError, KeyError, ValueError):
            return

    def get_pull(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PullResult:
        if not token:
            raise ProviderError("Gitea provider needs a token to query a PR.")
        status, body = self._curl(
            "GET", self._api(api_base, f"/repos/{repo}/pulls/{number}"), token,
        )
        if status != 200:
            raise ProviderError(f"Gitea PR #{number} lookup failed (HTTP {status}).")
        data = json.loads(body)
        # Gitea reports a merged PR as state "closed" + ``merged: true``; surface
        # the distinct "merged" state so reconciliation records it faithfully.
        state = str(data.get("state", "")) or "open"
        if data.get("merged"):
            state = "merged"
        return PullResult(
            url=str(data.get("html_url", "")),
            number=int(data.get("number", number)),
            state=state,
        )
