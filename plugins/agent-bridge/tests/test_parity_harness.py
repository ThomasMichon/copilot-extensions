"""Tests for the redacted remote venue parity harness."""

from __future__ import annotations

import json

import pytest

from agent_bridge import parity_harness as parity


class FakeClient:
    def __init__(self, *, cwd="/workspaces/example", capabilities=None):
        self.session = {
            "session_id": "session-1",
            "status": "idle",
            "acp_session_id": "acp-1",
            "pid": 123,
            "target_type": "command",
        }
        self.cwd = cwd
        self.capabilities = capabilities or ["example-local-skill"]
        self.events = []
        self.ended = False
        self.prompts = []

    def start_session(self, **kwargs):
        return {"session_id": "session-1", "name": "parity"}

    def get_session(self, session_id):
        return dict(self.session)

    def read_range(self, session_id, *, start=0, end=None):
        return [event for event in self.events if event["id"] >= start]

    def submit_prompt(self, session_id, prompt, **kwargs):
        self.prompts.append(prompt)
        if "VENUE_PARITY_JSON" in prompt:
            payload = {
                "capabilities": self.capabilities,
            }
            probe = {
                "cwd": self.cwd,
                "github_credential": True,
                "github_api": True,
                "ado_credential": True,
                "ado_ls_remote": True,
                "azure_token": True,
            }
            text = f"{parity._RESULT_MARKER}{json.dumps(payload)}"
            midpoint = len(text) // 2
            self.events.extend([
                {
                    "id": len(self.events) + 1,
                    "event": "tool_call_update",
                    "data": {
                        "raw_output": (
                            f"{parity._PROBE_MARKER}{json.dumps(probe)}"
                        )
                    },
                },
                {
                    "id": len(self.events) + 2,
                    "event": "agent_message",
                    "data": {"text": text[:midpoint]},
                },
                {
                    "id": len(self.events) + 3,
                    "event": "agent_message",
                    "data": {"text": text[midpoint:]},
                },
            ])
        else:
            self.events.append({
                "id": len(self.events) + 1,
                "event": "agent_message",
                "data": {"text": parity._REATTACH_MARKER},
            })
        self.events.append({
            "id": len(self.events) + 1,
            "event": "turn_complete",
            "data": {"stop_reason": "end_turn"},
        })
        self.session["status"] = "idle"
        return {"queued": False}

    def stop_session(self, session_id):
        self.session["status"] = "stopped"

    def resume_session(self, session_id, *, request_timeout=None):
        self.session["status"] = "idle"
        return dict(self.session)

    def end_session(self, session_id, *, force=False):
        self.ended = True

    def list_sessions(self):
        return [dict(self.session)]


def test_parity_run_emits_redacted_evidence_and_reattaches_same_child():
    client = FakeClient()

    result = parity.run(
        client,
        "container:example-1",
        expected_workspace="/workspaces/example",
        expected_capability="local-skill",
        auth=True,
        ado_url="https://example.visualstudio.com/Project/_git/repo",
        azure_scope="https://storage.azure.com/.default",
        startup_timeout=1,
        turn_timeout=1,
    )

    assert result.ok is True
    assert result.checks["same_acp_session"] is True
    assert result.checks["resumed_child_live"] is True
    assert result.observed["child_reused_on_stop_resume"] is True
    assert result.observed["github_credential"] is True
    assert result.observed["ado_ls_remote"] is True
    assert result.observed["azure_token"] is True
    assert client.ended is True
    assert all("password=" not in prompt for prompt in client.prompts)


def test_parity_run_fails_wrong_workspace_and_still_ends_session():
    client = FakeClient(cwd="/home/example")

    with pytest.raises(parity.ParityFailure, match="workspace"):
        parity.run(
            client,
            "codespace:example",
            expected_workspace="/workspaces/example",
            startup_timeout=1,
            turn_timeout=1,
        )

    assert client.ended is True


def test_parse_result_rejects_malformed_json():
    with pytest.raises(parity.ParityFailure, match="not valid JSON"):
        parity._parse_result(f"{parity._RESULT_MARKER}not-json")


def test_redaction_guard_rejects_credential_shape():
    with pytest.raises(parity.ParityFailure, match="credential-shaped"):
        parity._assert_redacted(["password=should-not-be-here"])


def test_start_timeout_cleans_unique_caller_session():
    class StartFailureClient(FakeClient):
        def start_session(self, **kwargs):
            self.session["caller_id"] = kwargs["caller_id"]
            raise TimeoutError("response lost")

    client = StartFailureClient()

    with pytest.raises(TimeoutError, match="response lost"):
        parity.run(
            client,
            "container:example-1",
            startup_timeout=1,
            turn_timeout=1,
        )

    assert client.ended is True


def test_quality_prompt_rejects_credentialed_ado_url():
    with pytest.raises(parity.ParityFailure, match="embedded credentials"):
        parity._quality_prompt(
            auth=True,
            ado_url="https://user:secret@example.visualstudio.com/P/_git/R",
            azure_scope=None,
        )
