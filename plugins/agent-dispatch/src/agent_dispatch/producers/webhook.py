"""Reactive producer -- turn inbound webhooks into tasks.

A tiny HTTP app (FastAPI, reusing the coordinator's existing deps) that maps
two generic, forge-neutral event shapes onto tasks:

* ``POST /webhook/pr`` -- a git-forge pull-request event. When the PR is
  **merged**, a follow-up task is created with ``source="pr-webhook"`` and
  ``origin_ref="pr/<n>"``, in the lane derived from the payload's repository
  remote. Handles the shape GitHub and Gitea share
  (``pull_request.merged`` / ``number`` / ``repository.clone_url``).
* ``POST /webhook/telemetry`` -- a monitoring alert. A **firing** alert
  creates a remediation task with ``source="telemetry"`` and
  ``origin_ref="<alert-id>"``. Accepts both an Alertmanager-style
  ``{"alerts": [...]}`` batch and a single flat alert object.

Every task carries a deterministic ``dedup_key`` so a redelivered webhook (or
a retry) doesn't double-enqueue. The app talks to the coordinator through an
ordinary :class:`DispatchClient` -- it is a *producer*, not part of the
coordinator core (which stays free of any PR/alert logic). This keeps the
public substrate generic; facility-specific routing (which forge, which
alertmanager, which lane) lives in the deployer's config, not here.

Config (JSON), all keys optional::

    {
      "url": "http://127.0.0.1:9330",   # coordinator (else AGENT_DISPATCH_URL)
      "default_repo": "example.com/acme/widget",
      "inbound_token": "shared-secret", # require this bearer on inbound hooks
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
      }
    }
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..client import DispatchClient
from ..identity import canonicalize_remote

ClientFactory = Callable[[], DispatchClient]


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
    "work this merge implies (deploy verification, changelog, downstream bumps)."
)
_DEFAULT_ALERT_TITLE = "Investigate alert: {name}"
_DEFAULT_ALERT_PROMPT = (
    "Alert {name} is {status} (severity {severity}) on {target}. Investigate "
    "and remediate."
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
    from fastapi import Body, FastAPI, Header, HTTPException

    cfg = config or {}
    default_repo = cfg.get("default_repo")
    inbound_token = cfg.get("inbound_token")
    pr_cfg = cfg.get("pr") or {}
    tel_cfg = cfg.get("telemetry") or {}

    if client_factory is None:
        coord_url = cfg.get("url") or os.environ.get("AGENT_DISPATCH_URL") or "http://127.0.0.1:9330"
        coord_token = cfg.get("coordinator_token") or os.environ.get("AGENT_DISPATCH_TOKEN")

        def client_factory() -> DispatchClient:  # type: ignore[misc]
            return DispatchClient(coord_url, token=coord_token)

    app = FastAPI(title="agent-dispatch webhook producer")

    def _guard(authorization: str | None) -> None:
        if inbound_token:
            expected = f"Bearer {inbound_token}"
            if authorization != expected:
                raise HTTPException(status_code=401, detail="invalid inbound token")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "producer": "webhook"}

    @app.post("/webhook/pr")
    def pr_hook(
        payload: dict = Body(...),  # noqa: B008 (FastAPI dependency-injection idiom)
        authorization: str | None = Header(default=None),
    ) -> dict:
        _guard(authorization)
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
    def telemetry_hook(
        payload: dict = Body(...),  # noqa: B008 (FastAPI dependency-injection idiom)
        authorization: str | None = Header(default=None),
    ) -> dict:
        _guard(authorization)
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
