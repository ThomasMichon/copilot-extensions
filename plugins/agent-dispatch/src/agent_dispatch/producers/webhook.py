"""Reactive producer -- turn inbound webhooks into tasks.

A tiny HTTP app (FastAPI, reusing the coordinator's existing deps) that maps
three generic, forge-neutral event shapes onto tasks:

* ``POST /webhook/pr`` -- a git-forge pull-request event. When the PR is
  **merged**, a follow-up task is created with ``source="pr-webhook"`` and
  ``origin_ref="pr/<n>"``, in the lane derived from the payload's repository
  remote. Handles the shape GitHub and Gitea share
  (``pull_request.merged`` / ``number`` / ``repository.clone_url``).
* ``POST /webhook/telemetry`` -- a monitoring alert. A **firing** alert
  creates a remediation task with ``source="telemetry"`` and
  ``origin_ref="<alert-id>"``. Accepts both an Alertmanager-style
  ``{"alerts": [...]}`` batch and a single flat alert object.
* ``POST /webhook/issue`` -- a git-forge issue event (GitHub's own
  ``issues`` webhook shape: ``action`` / ``issue`` / ``repository``, which
  Gitea mirrors closely enough to reuse the same extraction). Unlike the PR
  and telemetry hooks (one fixed template each), this route is driven by a
  list of **rules** (``config["issues"]``) so several independent label-
  watching backlogs (e.g. two different standing worker pools, each reacting
  to its own forge label) can share one listener and one config file. Each
  matching rule creates a task with ``source="issue-webhook"`` and
  ``origin_ref="issue/<n>"``. This is the reactive half of a
  "webhook-primary, polling-fallback" pair: a deployer-owned periodic poller
  can keep running on its own longer interval as a backstop for a
  missed/undelivered webhook, using the exact same ``<task_label>:<repo
  full name>#<issue number>`` dedup-key shape this route defaults to, so
  either path colliding on the same issue returns the same task rather than
  creating a duplicate **while that task is still non-terminal**. The
  coordinator's dedup index releases a key once its task reaches a terminal
  status (see :class:`DispatchClient`'s own ``create`` contract), so this is
  a *collision* guard against a still-in-flight duplicate, not a durable
  historical record -- a redelivery (or a poller run) arriving only after
  the first task already completed mints a fresh task rather than being
  recognized as a repeat. In practice this is bounded by the same
  originating issue: once the first task resolves it, a well-behaved
  poller/redelivery no longer finds an open issue to act on. Accepted,
  matching the identical tradeoff a deployer's own periodic poller already
  makes for the exact same dedup-key shape.

Every task carries a deterministic ``dedup_key`` so a redelivered webhook (or
a retry) doesn't double-enqueue **while the original task is still in
flight** (see the caveat above for the terminal-task case). The app talks to
the coordinator through an ordinary :class:`DispatchClient` -- it is a
*producer*, not part of the coordinator core (which stays free of any
PR/alert/issue logic). This keeps the public substrate generic;
deployment-specific routing (which forge, which alertmanager, which lane,
which label backlog) lives in the deployer's config, not here.

Config (JSON), all keys optional::

    {
      "url": "http://127.0.0.1:9847",   # coordinator (else AGENT_DISPATCH_URL)
      "default_repo": "example.com/acme/widget",
      "inbound_token": "shared-secret", # require this bearer on inbound hooks
      "github_secret": "webhook-secret", # verify GitHub's own X-Hub-Signature-256
      "pr": {
        "on_merged_only": true,
        "base_branches": ["main"],       # optional allowlist
        "title_template": "Follow up on merged PR #{number}: {pr_title}",
        "prompt_template": "PR #{number} ({url}) merged into {base}. ...",
        "require": ["reviewer"], "labels": ["pr-followup"], "proposed": false
      },
      "telemetry": {
        "on_status": ["firing"],
        "severities": ["critical", "warning"],   # optional allowlist
        "title_template": "Investigate alert: {name}",
        "prompt_template": "Alert {name} is {status} (severity {severity}) ...",
        "require": [], "labels": ["telemetry"], "proposed": false
      },
      "issues": [
        {
          "name": "ci-failure-fix-worker",         # identifies this rule in skip reasons
          "match_actions": ["opened", "labeled"],  # default: opened, labeled, label_updated
          "match_labels": ["ci-failure-signature"],# ALL must be present on the issue
          "repo_allowlist": ["acme/widget"],        # optional
          "repo": "example.com/acme/widget",        # dispatch lane (else default_repo)
          "task_label": "ci-failure-fix-worker",    # dedup-key prefix + default template field
          "title_template": "drive: {title}",
          "prompt_template": "Resolve {repo_full_name}#{number} ({url}): {title}\\n\\n{body}",
          "require": [], "labels": ["ci-failure-fix-worker"], "proposed": false
        }
      ]
    }
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from ..client import DispatchClient
from ..identity import canonicalize_remote
from . import UNTRUSTED_EXTERNAL_CONTENT_NOTE

ClientFactory = Callable[[], DispatchClient]


def _verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Verify GitHub's own ``X-Hub-Signature-256`` HMAC header -- GitHub's
    webhook-authenticity mechanism, distinct from the bearer-token
    ``inbound_token`` above (GitHub never sends a bearer header; it only
    ever signs the raw body with a shared secret). Mirrors
    ``github_pr_review_webhook.py``'s own ``_verify_signature``."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a webhook config file (an empty/absent file yields defaults)."""
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("webhook config must be a JSON object")
    return data


def _fmt(template: str, fields: dict[str, Any]) -> str:
    """``str.format`` that leaves unknown ``{placeholders}`` untouched."""

    class _Safe(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    return template.format_map(_Safe(fields))


# -- extraction (forge/monitor-neutral, tolerant of common shapes) -----------


def extract_pr(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a git-forge PR webhook into a flat dict, or ``None`` if the
    body isn't a recognizable PR event."""
    pr = payload.get("pull_request")
    if not isinstance(pr, dict):
        return None
    number = payload.get("number") or pr.get("number")
    if number is None:
        return None
    repo = payload.get("repository") or {}
    remote = repo.get("clone_url") or repo.get("html_url") or repo.get("ssh_url")
    base = pr.get("base") or {}
    merged = pr.get("merged") is True or payload.get("action") == "merged"
    return {
        "number": number,
        "pr_title": pr.get("title", ""),
        "url": pr.get("html_url") or pr.get("url", ""),
        "merged": merged,
        "base": base.get("ref", "") if isinstance(base, dict) else "",
        "repo_remote": remote,
    }


def extract_issue(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a git-forge issue webhook into a flat dict, or ``None`` if
    the body isn't a recognizable issue event. Handles the shape GitHub's
    ``issues`` event uses (Gitea's own issue webhook is close enough to the
    same ``action``/``issue``/``repository`` layout to reuse this)."""
    issue = payload.get("issue")
    if not isinstance(issue, dict):
        return None
    number = issue.get("number")
    # bool is an int subclass in Python -- exclude it explicitly, along
    # with any non-positive or non-integer value, so a malformed delivery
    # (e.g. {"number": ""}/False/an object) can't reach origin_ref/the
    # dedup key as an ambiguous identity.
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        return None
    repo = payload.get("repository")
    if not isinstance(repo, dict):
        # Without a recognizable repository object there is no repo to
        # resolve a lane/allowlist against -- treat this the same as any
        # other unrecognizable body (never silently fall back to "no
        # repository", which a rule with a fixed `repo` and no
        # `repo_allowlist` would otherwise happily process).
        return None
    repo_full_name = repo.get("full_name")
    if not isinstance(repo_full_name, str) or not repo_full_name:
        # An empty/missing/non-string full_name produces a shared,
        # ambiguous dedup-key/repo identity ("<label>:#<number>") that
        # distinct repositories' malformed events would collide on --
        # reject rather than let a fixed-lane, no-allowlist rule process
        # an issue whose actual source repo could not be determined.
        return None
    remote = repo.get("clone_url") or repo.get("html_url") or repo.get("ssh_url")
    raw_labels = issue.get("labels")
    if not isinstance(raw_labels, list):
        # Same reasoning as the malformed-repository case above: a rule
        # without match_labels would otherwise happily process an event
        # whose labels we could not actually read, silently bypassing the
        # documented label-filter behavior.
        return None
    labels = [
        label.get("name")
        for label in raw_labels
        if isinstance(label, dict) and isinstance(label.get("name"), str) and label.get("name")
    ]
    action = payload.get("action", "")
    # Gitea's 'label_updated' fires for both additions and removals
    # (distinguished by changes.added_labels / removed_labels), unlike
    # GitHub's 'labeled', which is add-only by construction. Compute
    # whether this specific delivery actually added a label so the
    # route can treat a removal-only 'label_updated' as the no-op it is,
    # rather than matching it the same as a true addition.
    label_was_added = action != "label_updated"
    if action == "label_updated":
        changes = payload.get("changes")
        added = changes.get("added_labels") if isinstance(changes, dict) else None
        label_was_added = isinstance(added, list) and len(added) > 0
    return {
        "number": number,
        "title": issue.get("title", ""),
        "body": issue.get("body") or "",
        "url": issue.get("html_url") or issue.get("url", ""),
        "labels": labels,
        "action": action,
        "label_was_added": label_was_added,
        "repo_remote": remote,
        "repo_full_name": repo_full_name,
    }


def _iter_alerts(payload: dict[str, Any]):
    """Yield one flat alert dict per alert in ``payload`` (batch or single)."""
    alerts = payload.get("alerts")
    if isinstance(alerts, list):
        for alert in alerts:
            if isinstance(alert, dict):
                labels = alert.get("labels") or {}
                yield {
                    "id": alert.get("fingerprint") or alert.get("id") or labels.get("alertname"),
                    "name": labels.get("alertname") or alert.get("name", ""),
                    "status": alert.get("status") or payload.get("status", ""),
                    "severity": labels.get("severity") or alert.get("severity", ""),
                    "target": labels.get("instance") or alert.get("target", ""),
                    "repo": alert.get("repo") or labels.get("repo"),
                }
        return
    yield {
        "id": payload.get("id") or payload.get("fingerprint") or payload.get("name"),
        "name": payload.get("name", ""),
        "status": payload.get("status", ""),
        "severity": payload.get("severity", ""),
        "target": payload.get("target") or payload.get("instance", ""),
        "repo": payload.get("repo"),
    }


# -- app ---------------------------------------------------------------------

_DEFAULT_PR_TITLE = "Follow up on merged PR #{number}: {pr_title}"
_DEFAULT_PR_PROMPT = (
    "Pull request #{number} ({url}) was merged into {base}. Do the follow-up "
    "work this merge implies (deploy verification, changelog, downstream bumps). "
    + UNTRUSTED_EXTERNAL_CONTENT_NOTE
)
_DEFAULT_ALERT_TITLE = "Investigate alert: {name}"
_DEFAULT_ALERT_PROMPT = (
    "Alert {name} is {status} (severity {severity}) on {target}. Investigate "
    "and remediate. " + UNTRUSTED_EXTERNAL_CONTENT_NOTE
)
_DEFAULT_ISSUE_TITLE = "{task_label}: {repo_full_name}#{number} {title}"
_DEFAULT_ISSUE_PROMPT = (
    "Issue {repo_full_name}#{number} ({url}) was {action}: {title}\n\n{body}\n\n"
    + UNTRUSTED_EXTERNAL_CONTENT_NOTE
)


def build_app(
    config: dict[str, Any] | None = None, *, client_factory: ClientFactory | None = None
):
    """Construct the webhook FastAPI app.

    ``client_factory`` (injectable for tests) returns a fresh
    :class:`DispatchClient` per request; the default reads ``config['url']`` /
    ``AGENT_DISPATCH_URL`` and ``config['coordinator_token']`` /
    ``AGENT_DISPATCH_TOKEN``.
    """

    cfg = config or {}
    default_repo = cfg.get("default_repo")
    inbound_token = cfg.get("inbound_token")
    github_secret = cfg.get("github_secret")
    pr_cfg = cfg.get("pr") or {}
    tel_cfg = cfg.get("telemetry") or {}
    issue_rules = cfg.get("issues") or []

    if client_factory is None:
        _default_url = "http://127.0.0.1:9847"  # marketplace-isolation: allow legacy-compatibility
        coord_url = cfg.get("url") or os.environ.get("AGENT_DISPATCH_URL") or _default_url
        coord_token = cfg.get("coordinator_token") or os.environ.get("AGENT_DISPATCH_TOKEN")

        def client_factory() -> DispatchClient:  # type: ignore[misc]
            return DispatchClient(coord_url, token=coord_token)

    app = FastAPI(title="agent-dispatch webhook producer")

    async def _guard_and_parse(
        request: Request,
        authorization: str | None,
        x_hub_signature_256: str | None,
    ) -> dict:
        """Authenticate the request, then parse and return its JSON body.

        Checks ``github_secret``'s HMAC signature first when configured
        (GitHub's own mechanism -- it never sends a bearer header), then
        falls back to the forge-neutral ``inbound_token`` bearer check.
        Reading the raw body here (rather than via FastAPI's ``Body(...)``
        injection) is what makes signature verification possible at all --
        a pydantic-parsed body no longer has the exact bytes GitHub signed.
        """
        body = await request.body()
        if github_secret:
            if not _verify_github_signature(github_secret, body, x_hub_signature_256):
                raise HTTPException(status_code=401, detail="invalid webhook signature")
        elif inbound_token:
            expected = f"Bearer {inbound_token}"
            if authorization != expected:
                raise HTTPException(status_code=401, detail="invalid inbound token")
        if not body:
            return {}
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise HTTPException(status_code=400, detail="invalid JSON body") from error
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")
        return payload

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "producer": "webhook"}

    @app.post("/webhook/pr")
    async def pr_hook(
        request: Request,
        authorization: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict:
        payload = await _guard_and_parse(request, authorization, x_hub_signature_256)
        pr = extract_pr(payload)
        if pr is None:
            return {"skipped": "not a pull-request event"}
        if pr_cfg.get("on_merged_only", True) and not pr["merged"]:
            return {"skipped": "PR not merged", "number": pr["number"]}
        allowed = pr_cfg.get("base_branches")
        if allowed and pr["base"] not in allowed:
            return {"skipped": f"base {pr['base']!r} not in allowlist", "number": pr["number"]}
        lane = canonicalize_remote(pr["repo_remote"]) or default_repo
        if not lane:
            raise HTTPException(status_code=422, detail="no repo (lane) resolvable from payload")
        fields = {
            "number": pr["number"], "pr_title": pr["pr_title"], "url": pr["url"],
            "base": pr["base"], "repo": lane,
        }
        with client_factory() as client:
            task = client.create(
                _fmt(pr_cfg.get("title_template", _DEFAULT_PR_TITLE), fields),
                repo=lane,
                prompt=_fmt(pr_cfg.get("prompt_template", _DEFAULT_PR_PROMPT), fields),
                proposed=bool(pr_cfg.get("proposed", False)),
                requires=pr_cfg.get("require", []),
                labels=pr_cfg.get("labels", []),
                affinity=pr_cfg.get("affinity", {}),
                source="pr-webhook",
                origin_ref=f"pr/{pr['number']}",
                dedup_key=f"pr-merged:{lane}:{pr['number']}",
            )
        return {"created": task}

    @app.post("/webhook/telemetry")
    async def telemetry_hook(
        request: Request,
        authorization: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict:
        payload = await _guard_and_parse(request, authorization, x_hub_signature_256)
        on_status = tel_cfg.get("on_status", ["firing"])
        severities = tel_cfg.get("severities")
        created: list[dict] = []
        skipped: list[dict] = []
        with client_factory() as client:
            for alert in _iter_alerts(payload):
                if not alert.get("id"):
                    skipped.append({"reason": "no alert id"})
                    continue
                if on_status and alert["status"] not in on_status:
                    skipped.append({"id": alert["id"], "reason": f"status {alert['status']!r}"})
                    continue
                if severities and alert["severity"] not in severities:
                    sev = alert["severity"]
                    skipped.append({"id": alert["id"], "reason": f"severity {sev!r}"})
                    continue
                lane = alert.get("repo") or default_repo
                if not lane:
                    skipped.append({"id": alert["id"], "reason": "no repo (lane)"})
                    continue
                fields = {
                    "id": alert["id"], "name": alert["name"], "status": alert["status"],
                    "severity": alert["severity"], "target": alert["target"], "repo": lane,
                }
                task = client.create(
                    _fmt(tel_cfg.get("title_template", _DEFAULT_ALERT_TITLE), fields),
                    repo=lane,
                    prompt=_fmt(tel_cfg.get("prompt_template", _DEFAULT_ALERT_PROMPT), fields),
                    proposed=bool(tel_cfg.get("proposed", False)),
                    requires=tel_cfg.get("require", []),
                    labels=tel_cfg.get("labels", []),
                    affinity=tel_cfg.get("affinity", {}),
                    source="telemetry",
                    origin_ref=str(alert["id"]),
                    dedup_key=f"alert:{lane}:{alert['id']}:{alert['status']}",
                )
                created.append(task)
        return {"created": created, "skipped": skipped}

    @app.post("/webhook/issue")
    async def issue_hook(
        request: Request,
        authorization: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict:
        payload = await _guard_and_parse(request, authorization, x_hub_signature_256)
        issue = extract_issue(payload)
        if issue is None:
            return {"skipped": "not an issue event"}
        created: list[dict] = []
        skipped: list[dict] = []
        issue_labels = set(issue["labels"])
        with client_factory() as client:
            for index, rule in enumerate(issue_rules):
                # Each rule needs a stable, unique identity: it seeds the
                # default dedup-key prefix, and two unnamed rules sharing
                # the fallback "issue-rule" would otherwise derive the same
                # dedup_key when they match the same issue in the same
                # lane -- the coordinator's dedup index then returns the
                # first rule's task for both, silently dropping the second
                # rule's "independent" create.
                name = rule.get("name") or f"issue-rule-{index}"
                match_actions = rule.get("match_actions") or [
                    "opened",
                    "labeled",  # GitHub's own action name for an added label
                    "label_updated",  # Gitea's equivalent (HookIssueLabelUpdated)
                ]
                if issue["action"] not in match_actions:
                    skipped.append({
                        "rule": name, "number": issue["number"],
                        "reason": f"action {issue['action']!r} not matched",
                    })
                    continue
                if issue["action"] == "label_updated" and not issue["label_was_added"]:
                    # A Gitea label_updated delivery for a pure removal (no
                    # changes.added_labels) is not a new-work signal -- the
                    # issue's current label set may still satisfy
                    # match_labels even though nothing was actually added,
                    # which would otherwise enqueue a spurious duplicate
                    # once an earlier task for this issue goes terminal.
                    skipped.append({
                        "rule": name, "number": issue["number"],
                        "reason": "label_updated with no added labels (removal-only)",
                    })
                    continue
                required_labels = set(rule.get("match_labels") or [])
                if required_labels and not required_labels.issubset(issue_labels):
                    skipped.append({
                        "rule": name, "number": issue["number"],
                        "reason": "label filter not satisfied",
                    })
                    continue
                repo_allowlist = rule.get("repo_allowlist")
                if repo_allowlist and issue["repo_full_name"] not in repo_allowlist:
                    skipped.append({
                        "rule": name, "number": issue["number"],
                        "reason": f"repo {issue['repo_full_name']!r} not in allowlist",
                    })
                    continue
                lane = rule.get("repo") or default_repo
                if not lane:
                    skipped.append({
                        "rule": name, "number": issue["number"], "reason": "no repo (lane)",
                    })
                    continue
                task_label = rule.get("task_label", name)
                fields = {
                    "number": issue["number"], "title": issue["title"], "body": issue["body"],
                    "url": issue["url"], "action": issue["action"], "repo": lane,
                    "repo_full_name": issue["repo_full_name"], "task_label": task_label,
                }
                custom_prompt = rule.get("prompt_template")
                if custom_prompt:
                    # The issue's own title/body is attacker-influenceable
                    # external content -- append the shared framing
                    # regardless of what the rule author's own template
                    # says, mirroring producers/evaluator.py's
                    # _emit_from_rule, so this guardrail doesn't depend on
                    # every rule remembering it.
                    prompt = _fmt(custom_prompt, fields) + " " + UNTRUSTED_EXTERNAL_CONTENT_NOTE
                else:
                    prompt = _fmt(_DEFAULT_ISSUE_PROMPT, fields)
                task = client.create(
                    _fmt(rule.get("title_template", _DEFAULT_ISSUE_TITLE), fields),
                    repo=lane,
                    prompt=prompt,
                    proposed=bool(rule.get("proposed", False)),
                    requires=rule.get("require", []),
                    labels=rule.get("labels", []),
                    affinity=rule.get("affinity", {}),
                    source="issue-webhook",
                    origin_ref=f"issue/{issue['number']}",
                    dedup_key=(
                        rule.get("dedup_key")
                        or f"{task_label}:{issue['repo_full_name']}#{issue['number']}"
                    ),
                )
                created.append(task)
        return {"created": created, "skipped": skipped}

    return app


def serve(
    config: dict[str, Any] | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 9331,
) -> None:
    """Bind and serve the webhook app (blocking)."""
    import uvicorn

    uvicorn.run(build_app(config), host=host, port=port, log_level="info")
