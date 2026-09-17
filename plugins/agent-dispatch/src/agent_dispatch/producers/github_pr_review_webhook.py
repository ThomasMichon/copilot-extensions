"""Reactive GitHub webhook receiver for the PR-state observation pipeline.

Phase 10 item 3's final wiring slice: turns real GitHub webhook deliveries
into calls against the pipeline built across this item's earlier slices --
:mod:`agent_dispatch.github_provider_adapter` (observe),
:mod:`agent_dispatch.pr_revision_evaluator` (classify staleness), and
:mod:`agent_dispatch.pr_observation_store` (persist) -- exactly the "webhooks
primary, polling fallback" design :mod:`agent_dispatch.pr_polling_policy`
declared.

A GitHub webhook delivery is used only as a **trigger**, never as the
observation itself: unlike this repo's existing forge-neutral
``producers/webhook.py`` (which reads fields directly off a PR-merge
payload), the fields ``observe_pr_state`` needs -- ``reviewDecision`` above
all -- are GraphQL-only aggregates GitHub's REST webhook payloads do not
carry. So this receiver's job is narrow: recognize that *something relevant
changed* on a PR, extract its ``(repo, number)``, and re-fetch full state
through the adapter rather than trying to reconstruct it from the webhook
body. This also means a late, out-of-order, or even duplicate delivery is
harmless -- each delivery just triggers a fresh, idempotent re-observation.

Recognized event types (``X-GitHub-Event``): ``pull_request`` (any action --
even a no-op action still warrants a fresh look), ``pull_request_review``,
``pull_request_review_thread``, ``check_suite``, ``check_run``. Legacy
``status`` events are deliberately out of scope: a commit status does not
carry a PR reference GitHub itself resolves for you, and every provider this
plugin targets already reports check state through ``check_suite``/
``check_run`` instead.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any

# Imported at module level (not lazily inside build_app, unlike this
# plugin's other webhook producer) because `Request` is used as a type
# annotation on a nested route handler: with `from __future__ import
# annotations` in effect, FastAPI resolves that annotation via
# `typing.get_type_hints(func, globalns=func.__globals__)`, which only
# sees names present in this *module's* globals -- a name imported inside
# `build_app` never lands there, and the annotation silently fails to
# resolve as the special `Request` type (misclassified as an unknown
# query parameter instead).
from fastapi import FastAPI, Header, HTTPException, Request

from ..pr_observation_store import PRObservationStore, record_observation
from ..pr_review_poll_loop import Observer

_RELEVANT_EVENTS = frozenset(
    {
        "pull_request",
        "pull_request_review",
        "pull_request_review_thread",
        "check_suite",
        "check_run",
    }
)


def extract_pr_ref(event_type: str, payload: Mapping[str, Any]) -> tuple[str, int] | None:
    """The ``(repo, number)`` this webhook delivery concerns, or ``None`` if
    it is not a recognized/relevant event or carries no resolvable PR.

    ``check_suite``/``check_run`` payloads carry their PR reference under
    ``check_suite``/``check_run`` -> ``pull_requests`` (a list -- only the
    first is used, matching GitHub's own "one PR per branch" assumption for
    same-repo checks; a check run against an unrelated branch legitimately
    carries an empty list, in which case there is nothing to observe).
    """
    if event_type not in _RELEVANT_EVENTS:
        return None
    repo = ((payload.get("repository") or {}).get("full_name")) or None
    if not isinstance(repo, str) or not repo:
        return None
    if event_type in ("pull_request", "pull_request_review", "pull_request_review_thread"):
        pr = payload.get("pull_request")
        number = pr.get("number") if isinstance(pr, Mapping) else None
    else:  # check_suite / check_run
        container = payload.get(event_type)
        pull_requests = container.get("pull_requests") if isinstance(container, Mapping) else None
        number = None
        if isinstance(pull_requests, list) and pull_requests:
            first = pull_requests[0]
            number = first.get("number") if isinstance(first, Mapping) else None
    if not isinstance(number, int):
        return None
    return repo, number


def _verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


def build_app(
    store: PRObservationStore,
    observe: Observer,
    *,
    webhook_secret: str | None = None,
):
    """Construct the FastAPI app. ``observe`` is injected (production wires
    :meth:`agent_dispatch.github_provider_adapter.GitHubPRAdapter.observe`;
    tests inject a fake) so this module never itself shells out to ``gh``.

    ``webhook_secret``, when set, requires and verifies GitHub's
    ``X-Hub-Signature-256`` HMAC header (GitHub's own webhook-authenticity
    mechanism -- distinct from the bearer-token ``inbound_token`` the
    forge-neutral ``producers/webhook.py`` uses, since GitHub itself only
    ever sends a signature, never a bearer token).
    """
    app = FastAPI(title="agent-dispatch GitHub PR-review webhook")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "producer": "github-pr-review-webhook"}

    @app.post("/webhook/github/pr-review")
    async def pr_review_hook(
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict:
        body = await request.body()
        if webhook_secret is not None and not _verify_signature(
            webhook_secret, body, x_hub_signature_256
        ):
            raise HTTPException(status_code=401, detail="invalid webhook signature")
        payload = json.loads(body or b"{}")
        if not x_github_event:
            raise HTTPException(status_code=400, detail="missing X-GitHub-Event header")
        ref = extract_pr_ref(x_github_event, payload)
        if ref is None:
            return {"skipped": f"no PR reference in a {x_github_event!r} event"}
        repo, number = ref
        current = observe(repo, number)
        evaluated = record_observation(store, repo, number, current, now=time.time())
        return {
            "repo": repo,
            "number": number,
            "approval_status": evaluated.approval_status.value,
            "mergeability": evaluated.mergeability.value,
        }

    return app


def serve(
    store: PRObservationStore,
    observe: Observer,
    *,
    webhook_secret: str | None = None,
    host: str = "127.0.0.1",
    port: int = 9332,
) -> None:
    """Bind and serve the webhook app (blocking)."""
    import uvicorn

    uvicorn.run(
        build_app(store, observe, webhook_secret=webhook_secret),
        host=host,
        port=port,
        log_level="info",
    )
