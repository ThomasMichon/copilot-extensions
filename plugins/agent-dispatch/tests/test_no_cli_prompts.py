"""Tests for the reachability-tiered no-local-CLI autopilot seed builder."""

from __future__ import annotations

import pytest

from agent_dispatch.no_cli_prompts import no_cli_autopilot_worker_prompt
from agent_dispatch.worker_charter import (
    AUTOPILOT_CHARTER_NAME,
    OPERATING_PROCEDURES_TEXT,
    charter_text,
)


def test_no_cli_prompt_inlines_both_charters_and_helper():
    prompt = no_cli_autopilot_worker_prompt(
        "task-123",
        worker_id="remote-worker-9",
        environment="codespace",
    )

    assert "task-123" in prompt
    assert "remote-worker-9" in prompt
    assert "without a local `agent-dispatch` CLI" in prompt
    assert OPERATING_PROCEDURES_TEXT in prompt
    assert charter_text(AUTOPILOT_CHARTER_NAME) in prompt
    assert "agent-dispatch charter show operating-procedures" not in prompt
    assert "agent-dispatch charter show autopilot" not in prompt
    assert "dispatch_http.py" in prompt
    assert "AGENT_DISPATCH_URL" in prompt
    assert "AGENT_DISPATCH_SHARED_URL" in prompt
    assert "python dispatch_http.py claim-eval task-123 --worker remote-worker-9" in prompt
    assert "python dispatch_http.py progress task-123 --worker remote-worker-9" in prompt
    assert "python dispatch_http.py complete task-123 --worker remote-worker-9" in prompt


def test_no_cli_prompt_codespace_uses_forwarded_env_contract():
    prompt = no_cli_autopilot_worker_prompt(
        "task-123",
        worker_id="remote-worker-9",
        environment="codespace",
    )

    assert "agent_codespaces._peer_launch.peer_environment()" in prompt
    assert "Use those exact values if they are present" in prompt


def test_no_cli_prompt_container_uses_forwarded_env_contract():
    prompt = no_cli_autopilot_worker_prompt(
        "task-123",
        worker_id="remote-worker-9",
        environment="container",
    )

    assert "agent_containers._peer_launch.peer_environment()" in prompt
    assert "Use those exact values if they are present" in prompt
    assert "If `python` is unavailable but `python3` exists" in prompt


def test_no_cli_prompt_machine_supports_shared_token_command():
    prompt = no_cli_autopilot_worker_prompt(
        "task-123",
        worker_id="remote-worker-9",
        environment="machine",
    )

    assert "AGENT_DISPATCH_SHARED_TOKEN_COMMAND" in prompt
    assert "do not assume SSH back to the origin" in prompt


def test_no_cli_prompt_unaffiliated_requires_explicit_channel():
    prompt = no_cli_autopilot_worker_prompt(
        "task-123",
        worker_id="remote-worker-9",
        environment="unaffiliated",
        repo="https://github.com/example/repo",
    )

    assert "may know nothing about agent-dispatch ahead of time" in prompt
    assert (
        "python dispatch_http.py claim-eval task-123 --worker remote-worker-9 "
        "--repo https://github.com/example/repo"
    ) in prompt


def test_no_cli_prompt_rejects_conflicting_claim_scope():
    with pytest.raises(ValueError, match="repo and all_repos"):
        no_cli_autopilot_worker_prompt(
            "task-123",
            worker_id="remote-worker-9",
            environment="codespace",
            repo="https://github.com/example/repo",
            all_repos=True,
        )


def test_no_cli_prompt_rejects_unknown_environment():
    with pytest.raises(ValueError, match="unknown no-CLI environment"):
        no_cli_autopilot_worker_prompt(
            "task-123",
            worker_id="remote-worker-9",
            environment="unknown",  # type: ignore[arg-type]
        )
