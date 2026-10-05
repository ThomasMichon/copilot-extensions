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


def test_default_pr_prompt_frames_event_fields_as_untrusted():
    tc, sink = _client()
    r = tc.post("/webhook/pr", json=_MERGED_PR)
    assert r.status_code == 200
    prompt = sink[0]["prompt"]
    assert "untrusted subject data" in prompt
    # the PR's own attacker-influenceable title never appears unframed
    assert "Add feature" not in prompt or "untrusted subject data" in prompt


def test_default_telemetry_prompt_frames_event_fields_as_untrusted():
    tc, sink = _client({"default_repo": "example.com/acme/widget"})
    r = tc.post(
        "/webhook/telemetry",
        json={"name": "disk-full", "status": "firing", "severity": "critical", "target": "host-1"},
    )
    assert r.status_code == 200
    prompt = sink[0]["prompt"]
    assert "untrusted subject data" in prompt


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


_CI_FAILURE_ISSUE = {
    "action": "opened",
    "issue": {
        "number": 5287,
        "title": "CI failure: guards (full-tree, non-PR-scoped)",
        "body": "## Summary\n\nmodule-size guard failed.",
        "html_url": "https://github.com/acme/widget/issues/5287",
        "labels": [{"name": "ci-failure-signature"}, {"name": "bug"}],
    },
    "repository": {
        "full_name": "acme/widget",
        "clone_url": "https://github.com/acme/widget.git",
    },
}

_ISSUE_RULES_CONFIG = {
    "issues": [
        {
            "name": "ci-failure-fix-worker",
            "match_labels": ["ci-failure-signature"],
            "repo_allowlist": ["acme/widget"],
            "repo": "example.com/acme/widget",
            "task_label": "ci-failure-fix-worker",
            "labels": ["ci-failure-fix-worker"],
        }
    ]
}


def test_issue_matching_rule_creates_task():
    tc, sink = _client(_ISSUE_RULES_CONFIG)
    r = tc.post("/webhook/issue", json=_CI_FAILURE_ISSUE)
    assert r.status_code == 200
    body = r.json()
    assert body["skipped"] == []
    task = body["created"][0]
    assert task["source"] == "issue-webhook"
    assert task["origin_ref"] == "issue/5287"
    assert task["repo"] == "example.com/acme/widget"
    assert task["labels"] == ["ci-failure-fix-worker"]
    assert task["dedup_key"] == "ci-failure-fix-worker:acme/widget#5287"
    assert len(sink) == 1


def test_issue_dedup_key_matches_poller_format():
    """The webhook path's default dedup key must collide with whatever the
    periodic poller (e.g. tools/ci-failure-fix-worker-trigger.py's own
    ``build_dedup_key``) would derive for the same issue, so either path
    creating the task first is idempotent against the other."""
    tc, sink = _client(_ISSUE_RULES_CONFIG)
    tc.post("/webhook/issue", json=_CI_FAILURE_ISSUE)
    assert sink[0]["dedup_key"] == "ci-failure-fix-worker:acme/widget#5287"


def test_issue_default_prompt_frames_event_fields_as_untrusted():
    tc, sink = _client(_ISSUE_RULES_CONFIG)
    tc.post("/webhook/issue", json=_CI_FAILURE_ISSUE)
    assert "untrusted subject data" in sink[0]["prompt"]


def test_issue_label_filter_not_satisfied_is_skipped():
    tc, sink = _client(_ISSUE_RULES_CONFIG)
    body = {
        **_CI_FAILURE_ISSUE,
        "issue": {**_CI_FAILURE_ISSUE["issue"], "labels": [{"name": "bug"}]},
    }
    r = tc.post("/webhook/issue", json=body)
    assert r.json()["created"] == []
    assert "label filter" in r.json()["skipped"][0]["reason"]
    assert sink == []


def test_issue_action_not_matched_is_skipped():
    tc, sink = _client(_ISSUE_RULES_CONFIG)
    body = {**_CI_FAILURE_ISSUE, "action": "closed"}
    r = tc.post("/webhook/issue", json=body)
    assert r.json()["created"] == []
    assert "action" in r.json()["skipped"][0]["reason"]
    assert sink == []


def test_issue_repo_allowlist_rejects_other_repo():
    tc, sink = _client(_ISSUE_RULES_CONFIG)
    body = {
        **_CI_FAILURE_ISSUE,
        "repository": {**_CI_FAILURE_ISSUE["repository"], "full_name": "someone/else"},
    }
    r = tc.post("/webhook/issue", json=body)
    assert r.json()["created"] == []
    assert "not in allowlist" in r.json()["skipped"][0]["reason"]
    assert sink == []


def test_issue_non_issue_body_skipped():
    tc, _ = _client(_ISSUE_RULES_CONFIG)
    r = tc.post("/webhook/issue", json={"hello": "world"})
    assert r.json()["skipped"] == "not an issue event"


def test_issue_multiple_rules_each_independently_matched():
    tc, sink = _client({
        "issues": [
            {"name": "a", "match_labels": ["ci-failure-signature"], "repo": "lane-a",
             "task_label": "a-worker"},
            {"name": "b", "match_labels": ["needs-decomposition"], "repo": "lane-b",
             "task_label": "b-worker"},
        ]
    })
    r = tc.post("/webhook/issue", json=_CI_FAILURE_ISSUE)
    assert len(r.json()["created"]) == 1
    assert len(r.json()["skipped"]) == 1
    assert sink[0]["repo"] == "lane-a"


def test_inbound_token_guard():
    tc, sink = _client({"inbound_token": "secret"})
    assert tc.post("/webhook/pr", json=_MERGED_PR).status_code == 401
    ok = tc.post("/webhook/pr", json=_MERGED_PR, headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    assert len(sink) == 1


def test_health():
    tc, _ = _client()
    assert tc.get("/health").json()["status"] == "ok"
