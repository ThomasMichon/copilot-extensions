"""Gitea PR provider -- ``curl`` against the Gitea REST API.

Gitea has no installed CLI (``tea`` is absent on the deploy machines), so this
provider shells out to ``curl`` -- still "a CLI, not a Python HTTP library",
honoring the no-new-dependency constraint.  A token is required (resolved by
``base.resolve_token`` from ``pr.token_command`` / ``pr.token_env``).
"""

from __future__ import annotations

import json
import time

from ..pr_contract import PRSnapshot, Review
from .base import ProviderError, PRScope, PullResult, run_cli

# HTTP statuses (plus the synthetic 0 = curl-level failure) worth retrying when
# resolving/attaching labels.  A label apply is a small, idempotent call against
# the facility Gitea; a single transient hiccup must not silently drop a
# *required* label (auto-merge / source:<machine>).  4xx (auth / not-found /
# bad-request) is permanent and not retried.
_TRANSIENT_LABEL_HTTP = frozenset({0, 408, 429, 500, 502, 503, 504})
_LABEL_RETRIES = 3
_LABEL_BACKOFF = 0.5
# How many times to POST-then-verify the label set.  The bare POST can return
# 2xx yet a brand-new PR's labels occasionally don't reflect immediately under a
# burst (the create webhook fans out to the merge gate at the same instant), so
# we re-read the issue's labels and re-POST until the required ones are actually
# present -- "applied" means *verified present*, not "the POST returned 200".
_LABEL_ATTACH_ATTEMPTS = 3


def _is_transient(status: int) -> bool:
    """True when an HTTP status is worth retrying (network/5xx/429/408, or the
    synthetic 0 = curl-level failure); 4xx (auth/not-found/bad-request) is
    permanent.  Shared by the snapshot reads that back ``pr-watch``."""
    return status in _TRANSIENT_LABEL_HTTP or status >= 500


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
            result.label_error = self._apply_labels(scope, result.number, token)
        return result

    def _curl_with_retry(
        self,
        method: str,
        url: str,
        token: str,
        *,
        payload: dict | None = None,
    ) -> tuple[int, str]:
        """``_curl`` with bounded retry on transient failures.

        Returns ``(status, body)``; ``status == 0`` means a curl-level failure
        persisted across all attempts.  A *transient* status (5xx / 408 / 429 /
        curl error) is retried with exponential backoff; a permanent status
        (2xx success or a 4xx) returns immediately.  This is the linchpin of the
        label-apply reliability fix: the required ``source:<machine>`` labels
        live on label-list **page 2**, so any single un-retried blip on that
        page used to silently drop them (see ``_all_labels``).
        """
        delay = _LABEL_BACKOFF
        status, body = 0, ""
        for attempt in range(1, _LABEL_RETRIES + 1):
            try:
                status, body = self._curl(method, url, token, payload=payload)
            except ProviderError:
                status, body = 0, ""
            if status not in _TRANSIENT_LABEL_HTTP or attempt == _LABEL_RETRIES:
                return status, body
            time.sleep(delay)
            delay *= 2
        return status, body

    def _all_labels(self, scope: PRScope, token: str) -> dict[str, int]:
        """Resolve ``label-name (lowercased) -> id`` for the repo, **paginated**.

        Gitea's ``GET /repos/{repo}/labels`` returns a single page (default 30),
        so a repo with more labels than fit on page 1 leaves later labels
        invisible.  We page with an explicit ``limit`` until an **empty** page,
        so every label resolves regardless of how Gitea clamps the page size
        (stopping on "page shorter than the requested limit" would drop the
        newest labels whenever the server clamps ``limit`` below what we ask).

        On a page that still fails after retries this **raises** rather than
        returning a silent partial map -- a partial map is exactly what caused
        required ``source:<machine>`` labels (always on page 2) to be dropped
        whenever a single label-list GET hiccupped.  The caller turns the raise
        into a surfaced ``label_error`` (non-fatal to the PR, but visible).
        """
        by_name: dict[str, int] = {}
        page = 1
        page_size = 50
        while True:
            status, body = self._curl_with_retry(
                "GET",
                self._api(
                    scope.api_base,
                    f"/repos/{scope.repo}/labels?limit={page_size}&page={page}",
                ),
                token,
            )
            if status != 200:
                raise ProviderError(
                    f"Gitea label lookup failed (HTTP {status}) on page {page} "
                    f"for {scope.repo}"
                )
            batch = json.loads(body)
            if not isinstance(batch, list) or not batch:
                break
            for lbl in batch:
                if isinstance(lbl, dict) and lbl.get("name") and lbl.get("id") is not None:
                    by_name[str(lbl["name"]).lower()] = lbl["id"]
            page += 1
        return by_name

    def _apply_labels(self, scope: PRScope, number: int, token: str) -> str:
        """Resolve label names to ids and attach them; return an error string.

        Gitea's issue-label endpoint takes label **ids**, so names are mapped
        via the (paginated) repo label list first, then attached in a single
        POST.  Returns ``""`` on full success, or a human-readable description
        of what could not be applied (lookup failure, attach failure, or labels
        that don't exist in the repo).  Label trouble is **non-fatal** to the
        PR -- the caller surfaces the string as ``pr_label_error`` instead of
        silently swallowing it (the old behavior, which let a transient blip
        drop a required label with zero trace).
        """
        wanted = [name for name in scope.labels if name]
        if not wanted:
            return ""
        try:
            by_name = self._all_labels(scope, token)
        except (ProviderError, json.JSONDecodeError, ValueError) as exc:
            return f"label lookup failed: {exc}"

        resolved: dict[str, int] = {}
        missing: list[str] = []
        for name in wanted:
            lid = by_name.get(name.lower())
            if lid is None:
                missing.append(name)
            else:
                resolved[name] = lid

        problems: list[str] = []
        if resolved:
            attach_err = self._attach_labels_verified(scope, number, token, resolved)
            if attach_err:
                problems.append(attach_err)
        if missing:
            problems.append(f"labels not found in {scope.repo}: {missing}")
        return "; ".join(problems)

    def remove_label(
        self, repo: str, number: int, label: str, *, api_base: str = "",
        token: str | None = None,
    ) -> str:
        """Remove ``label`` from PR/issue ``number``; return an error string."""
        if not token:
            return "Gitea provider needs a token to remove a label."
        scope = PRScope(repo=repo, head="", base="", title="", api_base=api_base)
        try:
            by_name = self._all_labels(scope, token)
        except (ProviderError, json.JSONDecodeError, ValueError) as exc:
            return f"label lookup failed: {exc}"
        label_id = by_name.get(label.lower())
        if label_id is None:
            return f"label not found in {repo}: {label}"
        try:
            status, body = self._curl_with_retry(
                "DELETE",
                self._api(api_base, f"/repos/{repo}/issues/{number}/labels/{label_id}"),
                token,
            )
        except ProviderError as exc:
            return str(exc)
        if status in (200, 204, 404):
            return ""
        detail = body.strip()
        suffix = f": {detail[:200]}" if detail else ""
        return f"label removal failed (HTTP {status}) for {repo}#{number}{suffix}"

    def _issue_label_names(
        self, scope: PRScope, number: int, token: str
    ) -> set[str] | None:
        """Return the lowercased label names currently on the issue/PR.

        ``None`` means the read itself failed (so "are they present?" is
        unknown -- distinct from "present set is empty").
        """
        status, body = self._curl_with_retry(
            "GET",
            self._api(scope.api_base, f"/repos/{scope.repo}/issues/{number}/labels"),
            token,
        )
        if status != 200:
            return None
        try:
            arr = json.loads(body)
        except json.JSONDecodeError:
            return None
        if not isinstance(arr, list):
            return None
        return {
            str(lbl.get("name", "")).lower()
            for lbl in arr
            if isinstance(lbl, dict) and lbl.get("name")
        }

    def _attach_labels_verified(
        self, scope: PRScope, number: int, token: str, resolved: dict[str, int]
    ) -> str:
        """POST the resolved label ids, then **verify** they stuck; re-POST if not.

        Returns ``""`` once every requested label is confirmed present, or a
        description of what could not be confirmed after all attempts.  This is
        what turns "the POST returned 200" into "the labels are actually on the
        PR" -- the gap that let a required label silently fail to apply at PR
        creation even though the request appeared to succeed.
        """
        url = self._api(scope.api_base, f"/repos/{scope.repo}/issues/{number}/labels")
        want = {name.lower() for name in resolved}
        ids = sorted(resolved.values())
        last = ""
        present: set[str] | None = None
        for attempt in range(1, _LABEL_ATTACH_ATTEMPTS + 1):
            status, _ = self._curl_with_retry("POST", url, token, payload={"labels": ids})
            if status not in (200, 201):
                last = f"attach failed (HTTP {status})"
            present = self._issue_label_names(scope, number, token)
            if present is not None and want.issubset(present):
                return ""
            if present is None:
                last = last or "could not verify labels were applied"
            if attempt < _LABEL_ATTACH_ATTEMPTS:
                time.sleep(_LABEL_BACKOFF * attempt)
        still_missing = sorted(
            name for name in resolved if name.lower() not in (present or set())
        )
        if still_missing:
            return f"labels did not stick after retries: {still_missing}"
        return last

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
        merged = bool(data.get("merged"))
        state = str(data.get("state", "")) or "open"
        if merged:
            state = "merged"
        return PullResult(
            url=str(data.get("html_url", "")),
            number=int(data.get("number", number)),
            state=state,
            merged=merged,
        )

    def get_snapshot(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PRSnapshot:
        """Fetch the full review/mergeability/lifecycle snapshot for pr-watch.

        Two reads: the PR object (state, merged, mergeable, head sha, base ref,
        author, title, draft, labels) and the paginated reviews list.  The
        result feeds the provider-neutral ``pr_contract`` diff/classify without
        this provider knowing anything about transitions.
        """
        if not token:
            raise ProviderError("Gitea provider needs a token to query a PR.")
        status, body = self._curl(
            "GET", self._api(api_base, f"/repos/{repo}/pulls/{number}"), token,
        )
        if status != 200:
            raise ProviderError(
                f"Gitea PR #{number} snapshot failed (HTTP {status}).",
                transient=_is_transient(status),
            )
        try:
            pr = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Gitea returned non-JSON PR payload: {exc}") from exc
        if not isinstance(pr, dict):
            raise ProviderError(f"unexpected Gitea PR payload for {repo}#{number}")

        # Gitea computes ``mergeable`` asynchronously and may report it null on a
        # freshly-opened PR; only a real bool is a known state (else None).
        mergeable_raw = pr.get("mergeable")
        labels = tuple(
            str(lbl.get("name", ""))
            for lbl in (pr.get("labels") or [])
            if isinstance(lbl, dict) and lbl.get("name")
        )
        return PRSnapshot(
            pr_state=str(pr.get("state", "")) or "open",
            merged=bool(pr.get("merged", False)),
            head_sha=str((pr.get("head") or {}).get("sha", "")),
            base_ref=str((pr.get("base") or {}).get("ref", "")),
            reviews=self._all_review_objs(repo, number, api_base, token),
            author=str((pr.get("user") or {}).get("login", "")),
            mergeable=mergeable_raw if isinstance(mergeable_raw, bool) else None,
            labels=labels,
            title=str(pr.get("title", "")),
            draft=bool(pr.get("draft", False)),
        )

    def _all_review_objs(
        self, repo: str, number: int, api_base: str, token: str
    ) -> tuple[Review, ...]:
        """Fetch every review, paging the endpoint, as ``pr_contract.Review``s.

        Gitea paginates ``/pulls/{n}/reviews`` (default ~30) in ascending id
        order; the watcher keys off the highest review id, so a missed later
        page would make the newest reviews invisible and hang the wait.  Pages
        with an explicit limit until an empty (or short) page.
        """
        reviews: list[Review] = []
        page = 1
        page_size = 50
        while True:
            status, body = self._curl_with_retry(
                "GET",
                self._api(
                    api_base,
                    f"/repos/{repo}/pulls/{number}/reviews?limit={page_size}&page={page}",
                ),
                token,
            )
            if status != 200:
                raise ProviderError(
                    f"Gitea reviews lookup failed (HTTP {status}) on page {page} "
                    f"for {repo}#{number}",
                    transient=_is_transient(status),
                )
            try:
                batch = json.loads(body)
            except json.JSONDecodeError as exc:
                raise ProviderError(f"Gitea returned non-JSON reviews: {exc}") from exc
            if not isinstance(batch, list) or not batch:
                break
            for r in batch:
                if not isinstance(r, dict):
                    continue
                reviews.append(
                    Review(
                        id=int(r.get("id", 0)),
                        state=str(r.get("state", "")),
                        user=str((r.get("user") or {}).get("login", "")),
                        submitted_at=str(r.get("submitted_at", "")),
                        commit_id=str(r.get("commit_id", "") or ""),
                        dismissed=bool(r.get("dismissed", False)),
                    )
                )
            if len(batch) < page_size:
                break
            page += 1
        return tuple(reviews)

    def add_label(
        self, repo: str, number: int, label: str, *, api_base: str = "",
        token: str | None = None,
    ) -> str:
        """Attach ``label`` to PR/issue ``number`` (verified); return "" on success.

        The consent primitive behind ``pr-merge``: resolve the label name to its
        id via the (paginated) repo label list, then POST-then-verify it is
        actually present (the same "applied == verified present" guarantee the
        create path uses).  Returns "" on success, or a human-readable error.
        """
        if not token:
            return "Gitea provider needs a token to add a label."
        scope = PRScope(repo=repo, head="", base="", title="", api_base=api_base)
        try:
            by_name = self._all_labels(scope, token)
        except (ProviderError, json.JSONDecodeError, ValueError) as exc:
            return f"label lookup failed: {exc}"
        label_id = by_name.get(label.lower())
        if label_id is None:
            return f"label not found in {repo}: {label}"
        return self._attach_labels_verified(scope, number, token, {label: label_id})

    def list_open_pulls(
        self, repo: str, *, api_base: str = "", token: str | None = None
    ) -> tuple[int, ...]:
        """Return the numbers of every open PR on ``repo`` (paginated).

        The sweep input for ``pr-merge --all``: each number is then snapshotted
        + classified individually, so this returns just the identifiers.
        """
        if not token:
            raise ProviderError("Gitea provider needs a token to list PRs.")
        scope = PRScope(repo=repo, head="", base="", title="", api_base=api_base)
        numbers: list[int] = []
        page = 1
        page_size = 50
        while True:
            status, body = self._curl_with_retry(
                "GET",
                self._api(scope.api_base,
                          f"/repos/{repo}/pulls?state=open&limit={page_size}&page={page}"),
                token,
            )
            if status != 200:
                raise ProviderError(
                    f"Gitea open-PR list failed (HTTP {status}) on page {page} "
                    f"for {repo}",
                    transient=_is_transient(status),
                )
            try:
                batch = json.loads(body)
            except json.JSONDecodeError as exc:
                raise ProviderError(f"Gitea returned non-JSON PR list: {exc}") from exc
            if not isinstance(batch, list) or not batch:
                break
            for p in batch:
                if isinstance(p, dict) and p.get("number") is not None:
                    numbers.append(int(p["number"]))
            if len(batch) < page_size:
                break
            page += 1
        return tuple(numbers)
