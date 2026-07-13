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

from ..pr_contract import Comment, CommentThread, ThreadsResult
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
        """Not implemented: pr-watch/pr-merge snapshot flow is gitea-only today."""
        _ = (repo, api_base, token)
        raise ProviderError(
            f"Provider '{self.name}' does not support listing open PRs "
            "(pr-merge --all is gitea-only today)."
        )

    def request_auto_complete(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None,
        automerge_label: str = "", squash: bool = True,
        delete_source_branch: bool = True, bypass_policy: bool = False,
        bypass_reason: str = "",
    ) -> str:
        """Request auto-complete by applying the consent label (the GitHub way).

        GitHub's merge mechanism here is the ``automerge_label`` the review gate
        watches (via ``gh pr edit --add-label``); the squash / delete-source /
        bypass options do not apply.
        """
        _ = (api_base, squash, delete_source_branch, bypass_policy, bypass_reason)
        if not automerge_label:
            return "github: no automerge_label bound to signal merge consent."
        proc = run_cli(
            [
                "gh", "pr", "edit", str(number),
                "--repo", repo,
                "--add-label", automerge_label,
            ],
            env=self._env(token),
        )
        if proc.returncode != 0:
            return (
                f"gh pr edit --add-label failed for {repo}#{number}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return ""

    _THREADS_QUERY = (
        "query($owner:String!,$name:String!,$number:Int!){"
        "repository(owner:$owner,name:$name){pullRequest(number:$number){"
        "reviewThreads(first:100){nodes{id isResolved isOutdated "
        "path comments(first:50){nodes{author{login} body}}}}}}}"
    )

    @staticmethod
    def _split_owner_name(repo: str) -> tuple[str, str]:
        if "/" not in repo:
            raise ProviderError(f"GitHub repo must be 'owner/name', got '{repo}'.")
        owner, name = repo.split("/", 1)
        return owner, name

    def _graphql(self, query: str, token: str | None, **fields) -> tuple[dict, str]:
        args = ["gh", "api", "graphql", "-f", f"query={query}"]
        for k, v in fields.items():
            # -F coerces ints/bools; string node ids also pass fine via -F.
            args += ["-F", f"{k}={v}"]
        proc = run_cli(args, env=self._env(token))
        if proc.returncode != 0:
            return {}, (proc.stderr.strip() or proc.stdout.strip())
        try:
            return json.loads(proc.stdout or "{}"), ""
        except json.JSONDecodeError as exc:
            return {}, f"bad GraphQL JSON: {exc}"

    def get_comment_threads(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> ThreadsResult:
        """List PR review threads via GraphQL (GitHub's irritating detail).

        GitHub review threads have opaque node ids, so the returned
        ``CommentThread.id`` is a display index; :meth:`resolve_threads` resolves
        by re-fetching node ids (it resolves all active threads, not by index).
        """
        _ = api_base
        owner, name = self._split_owner_name(repo)
        data, err = self._graphql(
            self._THREADS_QUERY, token, owner=owner, name=name, number=number
        )
        if err:
            return ThreadsResult(supported=True, error=f"gh graphql threads: {err}")
        nodes = (
            data.get("data", {}).get("repository", {}).get("pullRequest", {})
            .get("reviewThreads", {}).get("nodes", [])
        )
        threads: list[CommentThread] = []
        for i, t in enumerate(nodes):
            if not isinstance(t, dict):
                continue
            comments = tuple(
                Comment(
                    author=str((c.get("author") or {}).get("login", "")),
                    content=str(c.get("body", "")).strip(),
                )
                for c in (t.get("comments", {}) or {}).get("nodes", [])
                if isinstance(c, dict) and str(c.get("body", "")).strip()
            )
            if not comments:
                continue
            if t.get("isResolved"):
                status = "resolved"
            elif t.get("isOutdated"):
                status = "outdated"
            else:
                status = "active"
            threads.append(
                CommentThread(
                    id=i + 1, status=status,
                    file_path=str(t.get("path", "") or ""), comments=comments,
                )
            )
        return ThreadsResult(threads=tuple(threads))

    _RESOLVE_MUTATION = (
        "mutation($id:ID!){resolveReviewThread(input:{threadId:$id})"
        "{thread{isResolved}}}"
    )

    def resolve_threads(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None,
        thread_ids: tuple[int, ...] = (),
    ) -> str:
        """Resolve all active review threads via GraphQL.

        GitHub thread ids are opaque node ids, so ``thread_ids`` (display
        indices) cannot target individually; this resolves every currently
        unresolved thread (the "addressed all feedback" case).
        """
        _ = (api_base, thread_ids)
        owner, name = self._split_owner_name(repo)
        data, err = self._graphql(
            self._THREADS_QUERY, token, owner=owner, name=name, number=number
        )
        if err:
            return f"gh graphql threads: {err}"
        nodes = (
            data.get("data", {}).get("repository", {}).get("pullRequest", {})
            .get("reviewThreads", {}).get("nodes", [])
        )
        errors: list[str] = []
        for t in nodes:
            if not isinstance(t, dict) or t.get("isResolved") or not t.get("id"):
                continue
            _res, merr = self._graphql(self._RESOLVE_MUTATION, token, id=t["id"])
            if merr:
                errors.append(merr)
        return "; ".join(errors)
