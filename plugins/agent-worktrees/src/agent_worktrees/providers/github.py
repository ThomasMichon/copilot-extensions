"""GitHub PR provider -- the ``gh`` CLI.

Uses ``gh pr create`` (and ``gh pr view``) so it inherits ``gh``'s ambient
auth.  An explicit token (``pr.token_command`` / ``pr.token_env``) is passed
via ``GH_TOKEN`` when configured; otherwise ``gh``'s logged-in account is
used (the resolve_token None case).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import quote

from .base import ProviderError, PRScope, PullResult, run_cli

if TYPE_CHECKING:
    from ..pr_contract import PRSnapshot


class GitHubProvider:
    """Open + query pull requests on GitHub via the ``gh`` CLI."""

    name = "github"

    def _env(self, token: str | None) -> dict[str, str]:
        return {"GH_TOKEN": token} if token else {}

    def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
        args = [
            "gh", "pr", "create",
            "--repo", scope.repo,
            "--head", scope.head,
            "--base", scope.base,
            "--title", scope.title,
            "--body", scope.body,
        ]
        for label in scope.labels:
            args += ["--label", label]
        proc = run_cli(args, env=self._env(token))
        if proc.returncode != 0:
            raise ProviderError(
                f"gh pr create failed for {scope.repo} "
                f"{scope.head}->{scope.base}: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        # gh prints the PR URL on stdout; derive the number from the trailing path.
        url = proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""
        number = self._number_from_url(url)
        return PullResult(url=url, number=number, state="open")

    @staticmethod
    def _number_from_url(url: str) -> int | None:
        tail = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
        return int(tail) if tail.isdigit() else None

    def get_pull(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PullResult:
        proc = run_cli(
            [
                "gh", "pr", "view", str(number),
                "--repo", repo,
                "--json", "url,number,state",
            ],
            env=self._env(token),
        )
        if proc.returncode != 0:
            raise ProviderError(
                f"gh pr view #{number} failed for {repo}: {proc.stderr.strip()}"
            )
        data = json.loads(proc.stdout)
        # gh reports state as OPEN | CLOSED | MERGED; "merged" is the
        # authoritative landed signal.
        state = str(data.get("state", "open")).lower() or "open"
        return PullResult(
            url=str(data.get("url", "")),
            number=int(data.get("number", number)),
            state=state,
            merged=(state == "merged"),
        )

    def remove_label(
        self, repo: str, number: int, label: str, *, api_base: str = "",
        token: str | None = None,
    ) -> str:
        """Remove ``label`` from an existing PR via ``gh api``."""
        _ = api_base
        label_path = quote(label, safe="")
        proc = run_cli(
            [
                "gh", "api",
                "--method", "DELETE",
                f"/repos/{repo}/issues/{number}/labels/{label_path}",
            ],
            env=self._env(token),
        )
        if proc.returncode == 0:
            return ""
        detail = (proc.stderr.strip() or proc.stdout.strip())
        if "HTTP 404" in detail or "Not Found" in detail:
            return ""
        return f"gh label removal failed for {repo}#{number}: {detail}"

    def get_snapshot(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PRSnapshot:
        """Not implemented: pr-watch/pr-status snapshot reads are gitea-only today."""
        from .base import _unsupported_snapshot
        _ = (repo, number, api_base, token)
        return _unsupported_snapshot(self.name)
