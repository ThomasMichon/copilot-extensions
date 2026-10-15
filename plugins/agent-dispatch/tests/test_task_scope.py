"""Unit tests for the task-scoped capability token primitive (no FastAPI).

agent-dispatch-fleet-authority effort, Phase 1 -- see
efforts/active/agent-dispatch-fleet-authority/README.md and issue #6617.
"""

from __future__ import annotations

import pytest
import base64
import hashlib
import hmac
import json

from agent_dispatch.task_scope import (
    TaskScopeTokenError,
    mint_task_scope_token,
    verify_task_scope_token,
)

CONTROL_TOKEN = "control-secret"


def _mint(**overrides):
    kwargs = dict(
        task_id="task-1",
        worker_id="worker-1",
        owner_session_id="session-1",
        epoch=3,
    )
    kwargs.update(overrides)
    return mint_task_scope_token(CONTROL_TOKEN, **kwargs)


def test_round_trip_mint_and_verify():
    token = _mint()
    claims = verify_task_scope_token(CONTROL_TOKEN, token)
    assert claims.task_id == "task-1"
    assert claims.worker_id == "worker-1"
    assert claims.owner_session_id == "session-1"
    assert claims.epoch == 3


def test_wrong_signing_key_is_rejected():
    token = _mint()
    with pytest.raises(TaskScopeTokenError):
        verify_task_scope_token("a-different-control-token", token)


def test_tampered_payload_is_rejected():
    token = _mint()
    payload, signature = token.rsplit(".", 1)
    # Flip the payload (any mutation invalidates the base64 content without
    # the signature matching) -- a forged claim, not just noise.
    tampered = payload[:-1] + ("A" if payload[-1] != "A" else "B") + "." + signature
    with pytest.raises(TaskScopeTokenError):
        verify_task_scope_token(CONTROL_TOKEN, tampered)


def test_malformed_token_is_rejected():
    with pytest.raises(TaskScopeTokenError):
        verify_task_scope_token(CONTROL_TOKEN, "not-a-real-token")


def test_expired_token_is_rejected():
    token = _mint(ttl_seconds=10.0, now=1_000_000.0)
    with pytest.raises(TaskScopeTokenError):
        verify_task_scope_token(CONTROL_TOKEN, token, now=1_000_011.0)


def test_token_still_valid_just_before_expiry():
    token = _mint(ttl_seconds=10.0, now=1_000_000.0)
    claims = verify_task_scope_token(CONTROL_TOKEN, token, now=1_000_009.0)
    assert claims.epoch == 3


def test_distinct_tasks_mint_distinct_tokens():
    a = _mint(task_id="task-a")
    b = _mint(task_id="task-b")
    assert a != b
    assert verify_task_scope_token(CONTROL_TOKEN, a).task_id == "task-a"
    assert verify_task_scope_token(CONTROL_TOKEN, b).task_id == "task-b"


@pytest.mark.parametrize("claims", [
    [], None, {"v": "v1"},
    {"v": "v1", "task_id": None, "worker_id": "w", "owner_session_id": "s",
     "epoch": 0, "generation": 0, "exp": float("inf")},
    {"v": "v1", "task_id": "t", "worker_id": "w", "owner_session_id": "s",
     "epoch": True, "generation": 0, "exp": float("nan")},
])
def test_signed_malformed_claims_fail_as_invalid_credentials(claims):
    payload = json.dumps(claims).encode()
    signature = hmac.new(CONTROL_TOKEN.encode(), payload, hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + signature
    with pytest.raises(TaskScopeTokenError):
        verify_task_scope_token(CONTROL_TOKEN, token)


@pytest.mark.parametrize("token", ["%%%." + "0" * 64, "a." + "\u00e9", "x" * 8193])
def test_invalid_encoding_is_a_credential_error(token):
    with pytest.raises(TaskScopeTokenError):
        verify_task_scope_token(CONTROL_TOKEN, token)
