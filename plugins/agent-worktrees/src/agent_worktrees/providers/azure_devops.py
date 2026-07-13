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
    from ..pr_contract import PRSnapshot, ThreadsResult

#: Canonical consent marker Azure DevOps emits in a snapshot's ``labels`` when
#: the PR has native auto-complete set. A repo on this provider binds
#: ``pr.automerge_label: auto-complete`` so the shared classifier's
#: ``consent_present`` check (``automerge_label in labels``) lights up uniformly.
AUTO_COMPLETE_MARKER = "auto-complete"

#: Azure DevOps AAD resource GUID -- the audience for a REST access token minted
#: from an ambient ``az login`` when no PAT is configured.
_ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"


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
        """Build a :class:`PRSnapshot` from ``az repos pr show``.

        Reviewer **votes** map onto the contract's review verdicts (10/5 approve
        -> APPROVED, -10/-5 -> CHANGES_REQUESTED, 0 -> no verdict). Because ADO
        votes are point-in-time (not an ordered review log), reviewers who
        requested changes are assigned the highest synthetic ids so the shared
        ``effective_verdict`` (latest-wins) correctly treats *any* rejection as
        the blocking verdict. Native ``mergeStatus`` maps onto ``mergeable``, and
        a set ``autoCompleteSetBy`` surfaces as the ``auto-complete`` consent
        marker in ``labels`` so the classifier's ``consent_present`` lights up.
        """
        from ..pr_contract import PRSnapshot

        if not api_base:
            raise ProviderError("Azure DevOps provider needs the org URL (api_base).")
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
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderError(f"az returned non-JSON PR payload: {exc}") from exc

        status = str(data.get("status", "active")).lower()
        merged = status == "completed"
        pr_state = "closed" if status in ("completed", "abandoned") else "open"
        # ADO mergeStatus: succeeded | conflicts | failure | rejectedByPolicy |
        # queued | notSet. Only "succeeded" is a known-good; a queued/notSet is
        # not-yet-computed (None); everything else is a block (False).
        ms = str(data.get("mergeStatus", "")).lower()
        mergeable: bool | None
        if ms == "succeeded":
            mergeable = True
        elif ms in ("", "queued", "notset"):
            mergeable = None
        else:
            mergeable = False

        labels = tuple(
            str(t.get("name", ""))
            for t in (data.get("labels") or [])
            if isinstance(t, dict) and t.get("name")
        )
        if data.get("autoCompleteSetBy"):
            labels = (*labels, AUTO_COMPLETE_MARKER)

        base_ref = str(data.get("targetRefName", "")).replace("refs/heads/", "")
        head_sha = str((data.get("lastMergeSourceCommit") or {}).get("commitId", ""))
        author = str((data.get("createdBy") or {}).get("displayName", "")
                     or (data.get("createdBy") or {}).get("uniqueName", ""))

        return PRSnapshot(
            pr_state=pr_state,
            merged=merged,
            head_sha=head_sha,
            base_ref=base_ref,
            reviews=self._reviews_from_show(data),
            author=author,
            mergeable=mergeable,
            labels=labels,
            title=str(data.get("title", "")),
            draft=bool(data.get("isDraft", False)),
        )

    @staticmethod
    def _reviews_from_show(data: dict) -> tuple:
        """Map ``az repos pr show`` reviewer votes onto contract ``Review``s."""
        from ..pr_contract import Review

        graded = []
        for r in data.get("reviewers") or []:
            if not isinstance(r, dict):
                continue
            vote = int(r.get("vote", 0) or 0)
            if vote == 0:
                continue  # no verdict cast yet
            state = "APPROVED" if vote > 0 else "CHANGES_REQUESTED"
            user = str(r.get("displayName") or r.get("uniqueName") or "")
            graded.append((state, user))
        # Rejections sort last -> highest id -> win the latest-wins reduction, so
        # any changes-requested vote is the effective (blocking) verdict.
        graded.sort(key=lambda sv: (0 if sv[0] == "APPROVED" else 1, sv[1]))
        return tuple(
            Review(id=i + 1, state=state, user=user)
            for i, (state, user) in enumerate(graded)
        )

    def request_auto_complete(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None,
        automerge_label: str = "", squash: bool = True,
        delete_source_branch: bool = True, bypass_policy: bool = False,
        bypass_reason: str = "",
    ) -> str:
        """Request the PR to merge via ``az repos pr update`` (no label on ADO).

        Two shapes, because ADO **rejects ``--bypass-policy`` together with
        ``--auto-complete``** ("The bypass option cannot be used with
        auto-complete"):

        - **``bypass_policy`` (self-complete).** Complete the PR *now*, past
          branch policy (``--status completed --bypass-policy true``). Needed for
          a default branch whose policy never auto-satisfies for our own PRs
          (e.g. a central governance status policy) -- auto-complete would wait
          forever. Mirrors ``tools/ado-pr.ps1``. Completion is async; the caller
          confirms via ``pr-status`` / ``get_pull``.
        - **no bypass.** Set native ``--auto-complete true``; ADO merges once
          every branch policy passes.

        Either way applies the squash / delete-source-branch completion options.
        """
        _ = automerge_label  # ADO has no consent label; completion is native
        if not api_base:
            return "Azure DevOps provider needs the org URL (api_base)."
        args = [
            "az", "repos", "pr", "update",
            "--id", str(number),
            "--organization", api_base,
            "--squash", "true" if squash else "false",
            "--delete-source-branch", "true" if delete_source_branch else "false",
            "--output", "json",
        ]
        if bypass_policy:
            # Direct completion past policy -- NOT auto-complete (they are
            # mutually exclusive in ADO).
            args += ["--status", "completed", "--bypass-policy", "true"]
            if bypass_reason:
                args += ["--bypass-policy-reason", bypass_reason]
        else:
            args += ["--auto-complete", "true"]
        proc = run_cli(args, env=self._env(token))
        if proc.returncode != 0:
            verb = "complete (bypass)" if bypass_policy else "auto-complete"
            return (
                f"az repos pr update --{verb} failed for {repo}#{number}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return ""

    def add_label(
        self, repo: str, number: int, label: str, *, api_base: str = "",
        token: str | None = None,
    ) -> str:
        """Not the consent mechanism on ADO -- auto-complete is native.

        (See :meth:`request_auto_complete`.) Kept for interface completeness.
        """
        _ = (repo, number, label, api_base, token)
        return (
            "add_label is not the merge mechanism for azure-devops "
            "(use request_auto_complete -- native auto-complete)."
        )

    def list_open_pulls(
        self, repo: str, *, api_base: str = "", token: str | None = None
    ) -> tuple[int, ...]:
        """Return the ids of every active PR on ``repo`` via ``az repos pr list``."""
        if not api_base:
            raise ProviderError("Azure DevOps provider needs the org URL (api_base).")
        project, name = self._split_repo(repo)
        proc = run_cli(
            [
                "az", "repos", "pr", "list",
                "--organization", api_base,
                "--project", project,
                "--repository", name,
                "--status", "active",
                "--output", "json",
            ],
            env=self._env(token),
        )
        if proc.returncode != 0:
            raise ProviderError(
                f"az repos pr list failed for {repo}: {proc.stderr.strip()}"
            )
        try:
            data = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise ProviderError(f"az returned non-JSON PR list: {exc}") from exc
        return tuple(
            int(p["pullRequestId"]) for p in data
            if isinstance(p, dict) and p.get("pullRequestId") is not None
        )

    # ── First-class comment threads (ADO REST; AAD bearer or PAT Basic) ────

    def _rest_base(self, api_base: str, repo: str, number: int) -> str:
        project, name = self._split_repo(repo)
        return (
            f"{api_base.rstrip('/')}/{project}/_apis/git/repositories/"
            f"{name}/pullRequests/{number}"
        )

    @staticmethod
    def _basic_auth(token: str) -> str:
        import base64

        return base64.b64encode(f":{token}".encode()).decode()

    def _auth_header(self, token: str | None) -> tuple[str, str]:
        """Return (Authorization header, error). PAT -> Basic; else AAD Bearer.

        With no PAT we mint an AAD access token from the ambient ``az login``
        (the org's Entra auth), so thread reads work without a PAT -- matching
        the ``az repos pr`` calls that already ride ``az login``.
        """
        if token:
            return f"Authorization: Basic {self._basic_auth(token)}", ""
        proc = run_cli([
            "az", "account", "get-access-token",
            "--resource", _ADO_RESOURCE,
            "--query", "accessToken", "--output", "tsv",
        ])
        if proc.returncode != 0 or not proc.stdout.strip():
            return "", (
                "no PAT configured and could not mint an AAD token via "
                f"'az account get-access-token': {proc.stderr.strip()}"
            )
        return f"Authorization: Bearer {proc.stdout.strip()}", ""

    def _rest_call(
        self, method: str, url: str, auth_header: str, payload: str | None = None
    ) -> tuple[int, str]:
        args = [
            "curl", "-sS", "-X", method, url,
            "-w", "\n%{http_code}",
            "-H", auth_header,
        ]
        if payload is not None:
            args += ["-H", "Content-Type: application/json", "-d", payload]
        proc = run_cli(args)
        if proc.returncode != 0 and not proc.stdout:
            return 0, proc.stderr.strip()
        out = proc.stdout or ""
        body, _, code = out.rpartition("\n")
        try:
            status = int(code.strip())
        except ValueError:
            status = 0
        return status, body

    def get_comment_threads(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> ThreadsResult:
        """List PR comment threads via the ADO REST API (system notes filtered)."""
        from ..pr_contract import Comment, CommentThread, ThreadsResult

        if not api_base:
            return ThreadsResult(
                supported=False,
                error="Azure DevOps provider needs the org URL (api_base).",
            )
        auth_header, auth_err = self._auth_header(token)
        if auth_err:
            return ThreadsResult(supported=False, error=auth_err)
        url = f"{self._rest_base(api_base, repo, number)}/threads?api-version=7.1"
        status, body = self._rest_call("GET", url, auth_header)
        if status != 200:
            return ThreadsResult(
                supported=True,
                error=f"threads GET returned HTTP {status}: {body[:200]}",
            )
        try:
            data = json.loads(body or "{}")
        except json.JSONDecodeError as exc:
            return ThreadsResult(supported=True, error=f"bad threads JSON: {exc}")
        threads: list[CommentThread] = []
        for t in data.get("value", []):
            if not isinstance(t, dict):
                continue
            comments = tuple(
                Comment(
                    author=str((c.get("author") or {}).get("displayName", "")),
                    content=str(c.get("content", "")).strip(),
                )
                for c in t.get("comments", [])
                if isinstance(c, dict)
                and str(c.get("commentType", "")).lower() != "system"
                and str(c.get("content", "")).strip()
            )
            if not comments:
                continue
            ctx = t.get("threadContext") or {}
            threads.append(
                CommentThread(
                    id=t.get("id"),
                    status=str(t.get("status", "")),
                    file_path=str(ctx.get("filePath", "") or ""),
                    comments=comments,
                )
            )
        return ThreadsResult(threads=tuple(threads))

    def resolve_threads(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None,
        thread_ids: tuple[int, ...] = (),
    ) -> str:
        """Mark active threads ``closed`` (all active, or the given ``thread_ids``)."""
        if not api_base:
            return "Azure DevOps provider needs the org URL (api_base)."
        auth_header, auth_err = self._auth_header(token)
        if auth_err:
            return auth_err
        if thread_ids:
            targets: tuple[int, ...] = tuple(int(t) for t in thread_ids)
        else:
            listing = self.get_comment_threads(
                repo, number, api_base=api_base, token=token
            )
            if not listing.supported or listing.error:
                return listing.error or "could not list threads to resolve"
            targets = tuple(t.id for t in listing.active if t.id is not None)
        errors: list[str] = []
        for tid in targets:
            url = (
                f"{self._rest_base(api_base, repo, number)}/threads/{tid}"
                "?api-version=7.1"
            )
            status, body = self._rest_call(
                "PATCH", url, auth_header, payload=json.dumps({"status": "closed"})
            )
            if status not in (200, 201):
                errors.append(f"#{tid}: HTTP {status} {body[:120]}")
        return "; ".join(errors)
