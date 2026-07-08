"""Tests for the reactive webhook producer."""

from __future__ import annotations

import pytest

from agent_dispatch.producers import webhook

fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402


class FakeClient:
    def __init__(self, sink):
        self.sink = sink

    def create(self, title, **kwargs):
        task = {"id": f"t{len(self.sink)}", "title": title, "status": "queued", **kwargs}
        self.sink.append(task)
        return task

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None


def _client(config=None):
    sink: list[dict] = []
    app = webhook.build_app(config or {}, client_factory=lambda: FakeClient(sink))
    return TestClient(app), sink


_MERGED_PR = {
    "action": "closed",
    "number": 42,
    "pull_request": {
        "number": 42,
        "title": "Add feature",
        "html_url": "https://example.com/acme/widget/pulls/42",
        "merged": True,
        "base": {"ref": "main"},
    },
    "repository": {"clone_url": "https://example.com/acme/widget.git"},
}


def test_pr_merged_creates_task():
    tc, sink = _client()
    r = tc.post("/webhook/pr", json=_MERGED_PR)
    assert r.status_code == 200
    task = r.json()["created"]
    assert task["source"] == "pr-webhook"
    assert task["origin_ref"] == "pr/42"
    assert task["repo"] == "example.com/acme/widget"  # canonicalized from clone_url
    assert task["dedup_key"] == "pr-merged:example.com/acme/widget:42"
    assert len(sink) == 1


def test_pr_unmerged_is_skipped():
    tc, sink = _client()
    body = {**_MERGED_PR, "pull_request": {**_MERGED_PR["pull_request"], "merged": False}}
    r = tc.post("/webhook/pr", json=body)
    assert r.json()["skipped"] == "PR not merged"
    assert sink == []


def test_pr_base_branch_allowlist():
    tc, sink = _client({"pr": {"base_branches": ["release"]}})
    r = tc.post("/webhook/pr", json=_MERGED_PR)
    assert "not in allowlist" in r.json()["skipped"]
    assert sink == []


def test_pr_non_pr_body_skipped():
    tc, _ = _client()
    r = tc.post("/webhook/pr", json={"hello": "world"})
    assert r.json()["skipped"] == "not a pull-request event"


def test_pr_no_lane_is_422():
    tc, _ = _client()
    body = {**_MERGED_PR, "repository": {}}
    r = tc.post("/webhook/pr", json=body)
    assert r.status_code == 422


def test_telemetry_firing_alert_creates_task():
    tc, _sink = _client({"default_repo": "example.com/acme/widget"})
    body = {
        "status": "firing",
        "alerts": [
            {
                "fingerprint": "abc123",
                "status": "firing",
                "labels": {"alertname": "DiskFull", "severity": "critical", "instance": "host-a"},
            }
        ],
    }
    r = tc.post("/webhook/telemetry", json=body)
    task = r.json()["created"][0]
    assert task["source"] == "telemetry"
    assert task["origin_ref"] == "abc123"
    assert task["dedup_key"] == "alert:example.com/acme/widget:abc123:firing"


def test_telemetry_resolved_alert_skipped():
    tc, _sink = _client({"default_repo": "example.com/acme/widget"})
    body = {"id": "a1", "name": "DiskFull", "status": "resolved", "severity": "critical"}
    r = tc.post("/webhook/telemetry", json=body)
    assert r.json()["created"] == []
    assert _sink == []


def test_telemetry_severity_allowlist():
    tc, _sink = _client(
        {"default_repo": "example.com/acme/widget", "telemetry": {"severities": ["critical"]}}
    )
    body = {"id": "a1", "name": "Noise", "status": "firing", "severity": "info"}
    r = tc.post("/webhook/telemetry", json=body)
    assert r.json()["created"] == []


def test_inbound_token_guard():
    tc, sink = _client({"inbound_token": "secret"})
    assert tc.post("/webhook/pr", json=_MERGED_PR).status_code == 401
    ok = tc.post("/webhook/pr", json=_MERGED_PR, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    assert len(sink) == 1


def test_health():
    tc, _ = _client()
    assert tc.get("/health").json()["status"] == "ok"
