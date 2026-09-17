"""Tests for the Phase 10 item 3 GitHub PR-review webhook receiver."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from agent_dispatch.github_provider_adapter import PRObservation
from agent_dispatch.pr_observation_store import PRObservationStore
from agent_dispatch.producers.github_pr_review_webhook import build_app, extract_pr_ref
from agent_dispatch.provider_state_machine import ApprovalStatus, Mergeability, Revision

fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402


def _observation(number: int = 42) -> PRObservation:
    return PRObservation(
        number=number,
        approval_status=ApprovalStatus.APPROVED,
        mergeability=Mergeability.CLEAN,
        holds=frozenset(),
        revision=Revision(diff_hash="head-sha", base_sha="base-sha"),
    )


# --- extract_pr_ref ----------------------------------------------------------


def test_pull_request_event_extracts_repo_and_number():
    payload = {
        "repository": {"full_name": "example/project"},
        "pull_request": {"number": 42},
    }
    assert extract_pr_ref("pull_request", payload) == ("example/project", 42)


def test_pull_request_review_event_extracts_repo_and_number():
    payload = {
        "repository": {"full_name": "example/project"},
        "pull_request": {"number": 7},
    }
    assert extract_pr_ref("pull_request_review", payload) == ("example/project", 7)


def test_check_suite_event_extracts_from_pull_requests_list():
    payload = {
        "repository": {"full_name": "example/project"},
        "check_suite": {"pull_requests": [{"number": 9}]},
    }
    assert extract_pr_ref("check_suite", payload) == ("example/project", 9)


def test_check_run_event_extracts_from_pull_requests_list():
    payload = {
        "repository": {"full_name": "example/project"},
        "check_run": {"pull_requests": [{"number": 11}]},
    }
    assert extract_pr_ref("check_run", payload) == ("example/project", 11)


def test_check_suite_with_no_associated_pull_requests_returns_none():
    payload = {
        "repository": {"full_name": "example/project"},
        "check_suite": {"pull_requests": []},
    }
    assert extract_pr_ref("check_suite", payload) is None


def test_unrecognized_event_type_returns_none():
    payload = {
        "repository": {"full_name": "example/project"},
        "pull_request": {"number": 1},
    }
    assert extract_pr_ref("issues", payload) is None


def test_missing_repository_returns_none():
    payload = {"pull_request": {"number": 1}}
    assert extract_pr_ref("pull_request", payload) is None


def test_missing_pull_request_number_returns_none():
    payload = {"repository": {"full_name": "example/project"}, "pull_request": {}}
    assert extract_pr_ref("pull_request", payload) is None


# --- webhook app ---------------------------------------------------------


def _client(store, observe, **kwargs):
    app = build_app(store, observe, **kwargs)
    return TestClient(app)


def test_relevant_event_triggers_observe_and_persists(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    calls: list[tuple[str, int]] = []

    def observe(repo: str, number: int) -> PRObservation:
        calls.append((repo, number))
        return _observation(number)

    client = _client(store, observe)
    response = client.post(
        "/webhook/github/pr-review",
        headers={"X-GitHub-Event": "pull_request"},
        json={
            "repository": {"full_name": "example/project"},
            "pull_request": {"number": 42},
        },
    )

    assert response.status_code == 200
    assert calls == [("example/project", 42)]
    assert store.get("example/project", 42) is not None


def test_irrelevant_event_is_skipped_without_observing(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    calls: list[tuple[str, int]] = []

    def observe(repo: str, number: int) -> PRObservation:
        calls.append((repo, number))
        return _observation(number)

    client = _client(store, observe)
    response = client.post(
        "/webhook/github/pr-review",
        headers={"X-GitHub-Event": "issues"},
        json={"repository": {"full_name": "example/project"}},
    )

    assert response.status_code == 200
    assert response.json()["skipped"]
    assert calls == []


def test_missing_event_header_is_rejected(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    client = _client(store, lambda repo, number: _observation(number))

    response = client.post("/webhook/github/pr-review", json={})

    assert response.status_code == 400


def test_health_endpoint(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    client = _client(store, lambda repo, number: _observation(number))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# --- signature verification ------------------------------------------------


def _signed(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_is_accepted(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    client = _client(store, lambda repo, number: _observation(number), webhook_secret="s3cret")
    payload = {
        "repository": {"full_name": "example/project"},
        "pull_request": {"number": 1},
    }
    body = json.dumps(payload).encode()

    response = client.post(
        "/webhook/github/pr-review",
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _signed("s3cret", body),
            "Content-Type": "application/json",
        },
        content=body,
    )

    assert response.status_code == 200


def test_invalid_signature_is_rejected(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    client = _client(store, lambda repo, number: _observation(number), webhook_secret="s3cret")
    payload = {
        "repository": {"full_name": "example/project"},
        "pull_request": {"number": 1},
    }
    body = json.dumps(payload).encode()

    response = client.post(
        "/webhook/github/pr-review",
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=deadbeef",
            "Content-Type": "application/json",
        },
        content=body,
    )

    assert response.status_code == 401


def test_missing_signature_is_rejected_when_secret_configured(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    client = _client(store, lambda repo, number: _observation(number), webhook_secret="s3cret")

    response = client.post(
        "/webhook/github/pr-review",
        headers={"X-GitHub-Event": "pull_request"},
        json={
            "repository": {"full_name": "example/project"},
            "pull_request": {"number": 1},
        },
    )

    assert response.status_code == 401
