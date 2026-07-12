"""Azure DevOps PR provider -- the ``az repos pr`` CLI.

Uses ``az repos pr create`` / ``az repos pr show``.  ADO auth is MSA-backed
for this org and ``az login`` AAD tokens are rejected by ADO git over HTTPS,
so unattended use needs a **PAT** (resolved via ``pr.token_command`` /
``pr.token_env`` and exported as ``AZURE_DEVOPS_EXT_PAT``).

``api_base`` is the org URL (``https://dev.azure.com/<org>``); the ``repo``
is ``project/repo``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .base import ProviderError, PRScope, PullResult, run_cli

if TYPE_CHECKING:
    from ..pr_contract import PRSnapshot


class AzureDevOpsProvider:
    """Open + query pull requests on Azure DevOps via the ``az`` CLI."""

    name = "azure-devops"

    def _env(self, token: str | None) -> dict[str, str]:
        return {"AZURE_DEVOPS_EXT_PAT": token} if token else {}

    @staticmethod
    def _split_repo(repo: str) -> tuple[str, str]:
        if "/" not in repo:
            raise ProviderError(
                f"Azure DevOps repo must be 'project/repo', got '{repo}'."
            )
        project, name = repo.split("/", 1)
        return project, name

    def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
        if not scope.api_base:
            raise ProviderError(
                "Azure DevOps provider needs 'pr.api_base' (the org URL, "
                "e.g. https://dev.azure.com/<org>)."
            )
        project, name = self._split_repo(scope.repo)
        args = [
            "az", "repos", "pr", "create",
            "--organization", scope.api_base,
            "--project", project,
            "--repository", name,
            "--source-branch", scope.head,
            "--target-branch", scope.base,
            "--title", scope.title,
            "--description", scope.body,
            "--output", "json",
        ]
        for label in scope.labels:
            args += ["--labels", label]
        proc = run_cli(args, env=self._env(token))
        if proc.returncode != 0:
            raise ProviderError(
                f"az repos pr create failed for {scope.repo} "
                f"{scope.head}->{scope.base}: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        data = json.loads(proc.stdout)
        number = data.get("pullRequestId")
        return PullResult(
            url=self._web_url(scope.api_base, project, name, number),
            number=int(number) if number is not None else None,
            state=str(data.get("status", "active")).lower(),
        )

    @staticmethod
    def _web_url(org: str, project: str, repo: str, number) -> str:
        if number is None:
            return ""
        return f"{org.rstrip('/')}/{project}/_git/{repo}/pullrequest/{number}"

    def get_pull(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PullResult:
        if not api_base:
            raise ProviderError("Azure DevOps provider needs the org URL (api_base).")
        project, name = self._split_repo(repo)
        proc = run_cli(
            [
                "az", "repos", "pr", "show",
                "--organization", api_base,
                "--id", str(number),
                "--output", "json",
            ],
            env=self._env(token),
        )
        if proc.returncode != 0:
            raise ProviderError(
                f"az repos pr show #{number} failed for {repo}: {proc.stderr.strip()}"
            )
        data = json.loads(proc.stdout)
        # Azure DevOps PR status: active | completed | abandoned. "completed"
        # means merged; canonicalize to the open|merged|closed vocab the
        # tracking record uses and expose the authoritative merged signal.
        status = str(data.get("status", "active")).lower()
        merged = (status == "completed")
        state = {"completed": "merged", "abandoned": "closed"}.get(status, "open")
        return PullResult(
            url=self._web_url(api_base, project, name, number),
            number=number,
            state=state,
            merged=merged,
        )

    def remove_label(
        self, repo: str, number: int, label: str, *, api_base: str = "",
        token: str | None = None,
    ) -> str:
        """Remove a label from an Azure DevOps PR.

        The current CLI-backed provider only applies labels at PR creation.
        """
        _ = (repo, number, label, api_base, token)
        return "remove_label is not supported for azure-devops provider"

    def get_snapshot(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PRSnapshot:
        """Not implemented: pr-watch/pr-status snapshot reads are gitea-only today."""
        from .base import _unsupported_snapshot
        _ = (repo, number, api_base, token)
        return _unsupported_snapshot(self.name)

    def add_label(
        self, repo: str, number: int, label: str, *, api_base: str = "",
        token: str | None = None,
    ) -> str:
        """Not implemented: pr-merge label-apply is gitea-only today."""
        _ = (repo, number, label, api_base, token)
        return f"add_label is not supported for {self.name} provider"

    def list_open_pulls(
        self, repo: str, *, api_base: str = "", token: str | None = None
    ) -> tuple[int, ...]:
        """Not implemented: pr-merge sweep is gitea-only today."""
        _ = (repo, api_base, token)
        raise ProviderError(
            f"Provider '{self.name}' does not support listing open PRs "
            "(pr-merge --all is gitea-only today)."
        )
