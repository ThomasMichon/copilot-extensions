"""GitHub PR provider -- the ``gh`` CLI.

Uses ``gh pr create`` (and ``gh pr view``) so it inherits ``gh``'s ambient
auth.  An explicit token (``pr.token_command`` / ``pr.token_env``) is passed
via ``GH_TOKEN`` when configured; otherwise ``gh``'s logged-in account is
used (the resolve_token None case).
"""

from __future__ import annotations

import json
import os
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from urllib.parse import quote

from ..pr_contract import Comment, CommentThread, PRSnapshot, Review, ThreadsResult
from .base import ProviderError, PRScope, PullResult, run_cli


# HTTP statuses worth retrying (network / 5xx / 429 / 408); 4xx (auth / not-found
# / bad-request) is permanent. gh surfaces the upstream status in its stderr as
# "HTTP <code>", so a snapshot read classifies retryability by scanning for one
# of these markers (mirrors gitea's numeric ``_is_transient``).
_GH_TRANSIENT_HTTP = ("http 408", "http 429", "http 500", "http 502",
                      "http 503", "http 504")


def _gh_detail_is_transient(detail: str) -> bool:
    """True when a ``gh`` failure string names a retryable condition.

    Scans the CLI error text for a transient HTTP status marker or a network-level
    hiccup (timeout / connection reset). Everything else -- a 4xx, a bad token, a
    missing PR -- is permanent, so ``pr-watch`` fails fast instead of hanging the
    full timeout on a guaranteed failure.
    """
    low = detail.lower()
    if any(marker in low for marker in _GH_TRANSIENT_HTTP):
        return True
    return any(
        sig in low
        for sig in ("timeout", "timed out", "connection reset",
                    "connection refused", "temporarily unavailable", "eof")
    )


class GitHubProvider:
    """Open + query pull requests on GitHub via the ``gh`` CLI."""

    name = "github"

    def authority_endpoint(self, api_base: str = "") -> str:
        configured = (api_base or "").strip()
        if configured:
            parsed = urlparse(
                configured if "://" in configured else f"//{configured}"
            )
            if parsed.hostname:
                endpoint = parsed.hostname.lower()
                if parsed.port is not None:
                    endpoint = f"{endpoint}:{parsed.port}"
                return endpoint
        return (os.environ.get("GH_HOST") or "github.com").strip().lower()

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
        if scope.draft:
            args.append("--draft")
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

    def observe_head(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PullResult:
        """Read the exact head and GitHub's HTTP ``Date`` in one response."""
        host = self.authority_endpoint(api_base)
        proc = run_cli(
            [
                "gh", "api", "--hostname", host, "--include",
                f"/repos/{repo}/pulls/{number}",
            ],
            env=self._env(token),
        )
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip()
            raise ProviderError(f"gh PR #{number} observation failed: {detail}")
        marker = "\n{"
        body_at = proc.stdout.find(marker)
        if body_at < 0:
            raise ProviderError(f"gh PR #{number} observation was malformed.")
        headers = proc.stdout[:body_at]
        body = proc.stdout[body_at + 1:]
        date_header = ""
        for line in headers.splitlines():
            name, sep, value = line.partition(":")
            if sep and name.strip().lower() == "date":
                date_header = value.strip()
        try:
            data = json.loads(body)
            observed_at = parsedate_to_datetime(date_header).isoformat()
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"gh PR #{number} observation lacked valid server evidence."
            ) from exc
        return PullResult(
            number=int(data.get("number", number)),
            head_sha=str((data.get("head") or {}).get("sha", "")),
            observed_at=observed_at,
        )

    def publish_source_marker(
        self,
        repo: str,
        number: int,
        marker: str,
        *,
        api_base: str = "",
        token: str | None = None,
    ) -> str:
        proc = run_cli(
            [
                "gh",
                "pr",
                "comment",
                str(number),
                "--repo",
                repo,
                "--body",
                marker,
            ],
            env=self._env(token),
        )
        if proc.returncode != 0:
            return (
                f"gh pr comment #{number} failed for {repo}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return ""

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

    def mark_ready(
        self, repo: str, number: int, *, api_base: str = "",
        token: str | None = None, title: str = "",
        wip_title_prefixes: tuple[str, ...] = (),
    ) -> str:
        """Move a native draft PR to ready-for-review via ``gh pr ready``."""
        _ = (api_base, title, wip_title_prefixes)
        proc = run_cli(
            ["gh", "pr", "ready", str(number), "--repo", repo],
            env=self._env(token),
        )
        if proc.returncode == 0:
            return ""
        detail = (proc.stderr.strip() or proc.stdout.strip())
        if "not a draft" in detail.lower() or "already ready" in detail.lower():
            return (
                f"PR #{number} in {repo} is not in draft state; nothing to "
                f"un-draft ({detail})."
            )
        return f"gh pr ready failed for {repo}#{number}: {detail}"

    def get_snapshot(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PRSnapshot:
        """Fetch the full review/mergeability/lifecycle snapshot for pr-watch.

        Mirrors the gitea provider over GitHub's REST API (whose ``pulls`` shape
        is near-identical): one read of the PR object (state, merged, mergeable,
        head sha, base ref, author, title, draft, labels) plus the paginated
        reviews list -- the REST ``/pulls/{n}/reviews`` endpoint, so each review
        carries the **numeric** ``id`` the watch cursor keys off (``gh pr view``
        only exposes GraphQL node ids). ``checks_state`` folds together GitHub's
        two independent signals -- legacy commit *statuses* and Actions
        *check-runs* -- into the provider-neutral vocabulary.

        ``api_base`` may identify a GitHub Enterprise host; otherwise the
        ambient ``GH_HOST`` or ``github.com`` is resolved once and passed
        explicitly to every snapshot read.
        """
        host = self.authority_endpoint(api_base)
        proc = run_cli(
            [
                "gh", "api", "--hostname", host,
                f"/repos/{repo}/pulls/{number}",
            ],
            env=self._env(token),
        )
        if proc.returncode != 0:
            detail = (proc.stderr.strip() or proc.stdout.strip())
            raise ProviderError(
                f"gh PR #{number} snapshot failed for {repo}: {detail}",
                transient=_gh_detail_is_transient(detail),
            )
        try:
            pr = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"gh returned non-JSON PR payload: {exc}") from exc
        if not isinstance(pr, dict):
            raise ProviderError(f"unexpected gh PR payload for {repo}#{number}")

        # GitHub computes ``mergeable`` asynchronously and reports it null on a
        # freshly-opened PR; only a real bool is a known state (else None). It
        # reflects **merge conflicts** only -- a policy block (failing required
        # checks/reviews) leaves mergeable True, which is why checks_state and
        # the reviews are read separately.
        mergeable_raw = pr.get("mergeable")
        labels = tuple(
            str(lbl.get("name", ""))
            for lbl in (pr.get("labels") or [])
            if isinstance(lbl, dict) and lbl.get("name")
        )
        head_sha = str((pr.get("head") or {}).get("sha", ""))
        # REST ``state`` is only open|closed; ``merged`` is a separate bool (a
        # merged PR is closed+merged).
        return PRSnapshot(
            pr_state="closed" if str(pr.get("state", "")).lower() == "closed" else "open",
            merged=bool(pr.get("merged", False)),
            head_sha=head_sha,
            base_ref=str((pr.get("base") or {}).get("ref", "")),
            updated_at=str(pr.get("updated_at", "") or ""),
            reviews=self._all_review_objs(repo, number, token, host=host),
            author=str((pr.get("user") or {}).get("login", "")),
            mergeable=mergeable_raw if isinstance(mergeable_raw, bool) else None,
            checks_state=self._combined_checks_state(repo, head_sha, token, host=host),
            labels=labels,
            title=str(pr.get("title", "")),
            draft=bool(pr.get("draft", False)),
        )

    def _all_review_objs(
        self, repo: str, number: int, token: str | None, *,
        host: str = "github.com",
    ) -> tuple[Review, ...]:
        """Fetch every review as ``pr_contract.Review``s, paging the endpoint.

        GitHub's REST ``/pulls/{n}/reviews`` paginates (default 30) in ascending
        id order; the watcher keys off the highest review id, so a missed later
        page would make the newest reviews invisible and hang the wait. Pages at
        an explicit ``per_page`` until a short/empty page. Best-effort: a page
        that fails to read stops paging with what was gathered rather than
        breaking the whole snapshot.
        """
        reviews: list[Review] = []
        page = 1
        page_size = 100
        while True:
            proc = run_cli(
                [
                    "gh", "api", "--hostname", host,
                    f"/repos/{repo}/pulls/{number}/reviews"
                    f"?per_page={page_size}&page={page}",
                ],
                env=self._env(token),
            )
            if proc.returncode != 0:
                break
            try:
                batch = json.loads(proc.stdout or "[]")
            except json.JSONDecodeError:
                break
            if not isinstance(batch, list) or not batch:
                break
            for r in batch:
                if not isinstance(r, dict):
                    continue
                rid = r.get("id")
                if not isinstance(rid, int):
                    continue
                state = str(r.get("state", "")).upper()
                reviews.append(
                    Review(
                        id=rid,
                        state=state,
                        user=str((r.get("user") or {}).get("login", "")),
                        submitted_at=str(r.get("submitted_at", "") or ""),
                        commit_id=str(r.get("commit_id", "") or ""),
                        dismissed=(state == "DISMISSED"),
                    )
                )
            if len(batch) < page_size:
                break
            page += 1
        return tuple(reviews)

    def _combined_checks_state(
        self, repo: str, sha: str, token: str | None, *,
        host: str = "github.com",
    ) -> str:
        """Provider-neutral CI rollup for ``sha`` over BOTH GitHub check systems.

        GitHub reports CI two independent ways and a repo may use either or both:
        legacy commit **statuses** (``/commits/{sha}/status``) and Actions
        **check-runs** (``/commits/{sha}/check-runs``). This folds them into the
        ``pr_contract`` vocabulary -- ``success`` | ``failure`` | ``pending`` |
        ``""`` (nothing configured / unknown) -- with **failure dominating
        pending dominating success**. Never raises: an unreadable endpoint
        contributes nothing (a fully-unreadable pair yields ``""``, which never
        fires ``checks_failed``), so a transient hiccup can't break the snapshot.
        """
        if not sha:
            return ""
        any_signal = False
        failure = False
        pending = False

        # 1. Legacy combined commit status.
        proc = run_cli(
            [
                "gh", "api", "--hostname", host,
                f"/repos/{repo}/commits/{sha}/status",
            ],
            env=self._env(token),
        )
        if proc.returncode == 0:
            try:
                data = json.loads(proc.stdout or "{}")
            except json.JSONDecodeError:
                data = {}
            if isinstance(data, dict):
                statuses = data.get("statuses")
                if isinstance(statuses, list) and statuses:
                    any_signal = True
                    raw = str(data.get("state", "")).strip().lower()
                    if raw in ("failure", "error"):
                        failure = True
                    elif raw == "pending":
                        pending = True

        # 2. Actions check-runs.
        cproc = run_cli(
            [
                "gh", "api", "--hostname", host,
                f"/repos/{repo}/commits/{sha}/check-runs",
            ],
            env=self._env(token),
        )
        if cproc.returncode == 0:
            try:
                cdata = json.loads(cproc.stdout or "{}")
            except json.JSONDecodeError:
                cdata = {}
            runs = cdata.get("check_runs") if isinstance(cdata, dict) else None
            if isinstance(runs, list) and runs:
                any_signal = True
                for run in runs:
                    if not isinstance(run, dict):
                        continue
                    status = str(run.get("status", "")).strip().lower()
                    if status != "completed":
                        pending = True
                        continue
                    conclusion = str(run.get("conclusion", "")).strip().lower()
                    # neutral / success / skipped don't fail the gate; the rest do.
                    if conclusion in (
                        "failure", "timed_out", "action_required", "cancelled",
                        "startup_failure", "stale",
                    ):
                        failure = True

        if not any_signal:
            return ""
        if failure:
            return "failure"
        if pending:
            return "pending"
        return "success"

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
        _ = (squash, delete_source_branch, bypass_policy, bypass_reason)
        if not automerge_label:
            return "github: no automerge_label bound to signal merge consent."
        host = self.authority_endpoint(api_base)
        proc = run_cli(
            [
                "gh", "api", "--hostname", host,
                "--method", "POST",
                f"repos/{repo}/issues/{number}/labels",
                "-f", f"labels[]={automerge_label}",
            ],
            env=self._env(token),
        )
        if proc.returncode != 0:
            return (
                f"gh api label apply failed for {repo}#{number}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return ""

    def merge_pull(
        self, repo: str, number: int, *, squash: bool = True, admin: bool = False,
        api_base: str = "", token: str | None = None,
    ) -> str:
        """Directly merge PR ``number`` via ``gh pr merge``.

        The ``pr-merge <#> --now`` submitter-direct primitive. ``--squash`` keeps
        the non-interactive merge method explicit; ``--admin`` is used only when
        the configured review is non-blocking. The source branch is deliberately
        **not** deleted, so ``finalize`` can affirm the merge.
        """
        _ = api_base
        args = ["gh", "pr", "merge", str(number), "--repo", repo]
        if squash:
            args.append("--squash")
        if admin:
            args.append("--admin")
        proc = run_cli(args, env=self._env(token))
        if proc.returncode != 0:
            return (
                f"gh pr merge failed for {repo}#{number}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return ""

    def enable_auto_merge(
        self, repo: str, number: int, *, squash: bool = True,
        api_base: str = "", token: str | None = None,
    ) -> str:
        """Arm GitHub native auto-merge: ``gh pr merge <n> --squash --auto`` (#225).

        No ``--admin``: auto-merge waits on required checks rather than bypassing
        them. The source branch is left in place so ``finalize`` can affirm the
        eventual merge. Returns "" once auto-merge is armed (the PR is NOT yet
        merged), or an error string so the caller falls back to a direct merge.
        """
        _ = api_base
        args = ["gh", "pr", "merge", str(number), "--repo", repo, "--auto"]
        if squash:
            args.append("--squash")
        proc = run_cli(args, env=self._env(token))
        if proc.returncode != 0:
            return (
                f"gh pr merge --auto failed for {repo}#{number}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return ""

    def get_repo_policy(
        self, repo: str, *, default_branch: str = "", api_base: str = "",
        token: str | None = None,
    ):
        """Read GitHub repo settings + branch protection into a ``RepoPolicy``.

        Two reads: ``gh api repos/<repo>`` (merge methods, native auto-merge,
        delete-branch-on-merge) and, best-effort, the default branch's protection
        (required approving reviews, required status checks). Never raises: a
        failed settings read yields ``RepoPolicy(supported=False, error=...)``;
        an unreadable/absent protection leaves those fields ``None``.
        """
        from ..pr_contract import RepoPolicy

        proc = run_cli(
            ["gh", "api", f"repos/{repo}"], env=self._env(token),
        )
        if proc.returncode != 0:
            return RepoPolicy(
                supported=False,
                error=f"gh api repos/{repo} failed: "
                      f"{proc.stderr.strip() or proc.stdout.strip()}",
            )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return RepoPolicy(supported=False, error=f"non-JSON repo payload: {exc}")

        def _b(key):
            v = data.get(key)
            return bool(v) if isinstance(v, bool) else None

        req_reviews: int | None = None
        req_checks: bool | None = None
        if default_branch:
            pproc = run_cli(
                ["gh", "api",
                 f"repos/{repo}/branches/{default_branch}/protection"],
                env=self._env(token),
            )
            if pproc.returncode == 0:
                try:
                    prot = json.loads(pproc.stdout)
                except json.JSONDecodeError:
                    prot = {}
                if isinstance(prot, dict):
                    rpr = prot.get("required_pull_request_reviews")
                    if isinstance(rpr, dict):
                        cnt = rpr.get("required_approving_review_count")
                        req_reviews = int(cnt) if isinstance(cnt, int) else 0
                    rsc = prot.get("required_status_checks")
                    req_checks = bool(rsc)
            elif "Not Found" in (pproc.stderr + pproc.stdout):
                # No protection configured on the default branch -> nothing gates.
                req_reviews, req_checks = 0, False

        return RepoPolicy(
            supported=True,
            allow_squash=_b("allow_squash_merge"),
            allow_merge_commit=_b("allow_merge_commit"),
            allow_rebase=_b("allow_rebase_merge"),
            allow_auto_merge=_b("allow_auto_merge"),
            delete_branch_on_merge=_b("delete_branch_on_merge"),
            required_approving_reviews=req_reviews,
            has_required_status_checks=req_checks,
        )

    def head_contained_in_base(
        self, repo: str, base: str, head_sha: str, *, api_base: str = "",
        token: str | None = None,
    ) -> bool | None:
        """Not implemented: the zombie-PR containment probe is Gitea-only today."""
        _ = (repo, base, head_sha, api_base, token)
        return None

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
