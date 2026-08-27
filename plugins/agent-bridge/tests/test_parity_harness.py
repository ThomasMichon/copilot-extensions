"""Tests for the redacted remote venue parity harness."""

from __future__ import annotations

import json

import pytest

from agent_bridge import __main__ as cli
from agent_bridge import parity_harness as parity
from agent_bridge.protocol import (
    CONTAINER_RECREATE_FAULT,
    FAILED_ACP_HANDSHAKE_FAULT,
)
from agent_bridge.session_host.host_index import HostIndex, HostRecord


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
        self.stop_calls = 0
        self.resume_calls = 0
        self.refresh_calls = 0
        self.relay_fault_calls = 0
        self.handshake_fault_calls = 0
        self.start_calls = 0
        self.normal_started = False
        self.container_recreate_calls = 0
        self.other_sessions = []

    def start_session(self, **kwargs):
        self.start_calls += 1
        if kwargs.get("parity_fault") == FAILED_ACP_HANDSHAKE_FAULT:
            self.handshake_fault_calls += 1
            return {
                "session_id": "failed-session",
                "name": "failed",
                "status": "ended",
                "parity_fault_result": {
                    "host_process_removed": True,
                    "child_process_removed": True,
                    "remote_authority_removed": True,
                    "provider_cleanup": True,
                    "session_row_removed": True,
                    "session_memory_removed": True,
                    "host_index_removed": True,
                    "forward_removed": True,
                    "relay_removed": True,
                    "target_lock_removed": True,
                    "codespace_claim_removed": True,
                    "ownership_retained": False,
                    "cleanup_confirmed": True,
                },
            }
        self.normal_started = True
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
        self.stop_calls += 1
        self.session["status"] = "stopped"

    def resume_session(self, session_id, *, request_timeout=None):
        self.resume_calls += 1
        self.session["status"] = "idle"
        return dict(self.session)

    def refresh_endpoint(self):
        self.refresh_calls += 1
        return True

    def interrupt_relays_for_parity(self, session_id, *, timeout=90):
        self.relay_fault_calls += 1
        return {
            "owner_count_before": 1,
            "owner_count_after": 1,
            "interrupted_count": 1,
            "all_recovered": True,
            "owner_identity_preserved": True,
            "processes_replaced": True,
        }

    def recreate_container_for_parity(self, session_id, *, timeout=600):
        self.container_recreate_calls += 1
        assert session_id == "session-1"
        self.events = []
        self.session = {
            "session_id": "session-2",
            "status": "idle",
            "acp_session_id": "acp-2",
            "pid": 456,
            "target_type": "command",
        }
        return {
            "old_session_id": "session-1",
            "replacement_session_id": "session-2",
            "old_acp_session_id": "acp-1",
            "replacement_acp_session_id": "acp-2",
            "old_container_id": "a" * 64,
            "replacement_container_id": "b" * 64,
            "old_host_pid": 300,
            "replacement_host_pid": 400,
            "old_child_pid": 123,
            "replacement_child_pid": 456,
            "container_identity_changed": True,
            "old_session_removed": True,
            "old_host_index_removed": True,
            "old_forward_removed": True,
            "old_relay_removed": True,
            "target_lock_transferred": True,
        }

    def end_session(self, session_id, *, force=False):
        self.ended = True

    def list_sessions(self):
        sessions = (
            [dict(self.session)]
            if self.normal_started or self.session.get("caller_id")
            else []
        )
        return [*sessions, *self.other_sessions]


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


def test_frontend_restart_fault_gates_same_host_and_child():
    client = FakeClient()

    def fault_handler(session_id, timeout):
        assert session_id == "session-1"
        assert timeout == 1
        return {
            "frontend_pid_before": 100,
            "frontend_pid_after": 200,
            "host_index_target_removed": True,
            "initial_host_pid": 300,
            "recovered_host_pid": 300,
            "initial_child_pid": 123,
            "recovered_child_pid": 123,
            "recovered_from_remote_authority": True,
        }

    result = parity.run(
        client,
        "container:example-1",
        startup_timeout=1,
        turn_timeout=1,
        fault=parity.FRONTEND_RESTART_HOSTINDEX_LOSS,
        fault_handler=fault_handler,
    )

    assert result.ok is True
    assert result.initial_host_pid == 300
    assert result.resumed_host_pid == 300
    assert result.checks["frontend_restarted"] is True
    assert result.checks["host_index_target_removed"] is True
    assert result.checks["recovered_from_remote_authority"] is True
    assert result.checks["same_host"] is True
    assert result.checks["same_child"] is True
    assert client.stop_calls == 0
    assert client.resume_calls == 0
    assert client.refresh_calls == 1


def test_frontend_restart_fault_refuses_other_active_session():
    client = FakeClient()
    client.other_sessions = [{"session_id": "other", "status": "idle"}]

    with pytest.raises(
        parity.ParityFailure,
        match="another managed session is active: other",
    ):
        parity.run(
            client,
            "container:example-1",
            startup_timeout=1,
            turn_timeout=1,
            fault=parity.FRONTEND_RESTART_HOSTINDEX_LOSS,
            fault_handler=lambda _session_id, _timeout: {},
        )

    assert client.ended is True


def test_frontend_restart_fault_removes_only_target_and_recovers_from_authority(
    tmp_path,
    monkeypatch,
    capsys,
):
    index_path = tmp_path / "hosts" / "index.json"
    initial = HostRecord(
        session_id="session-1",
        port=5000,
        host_pid=300,
        child_pid=123,
        boundary="container",
        extra={"remote_authority_v2": True},
    )
    other = HostRecord(
        session_id="other",
        port=5001,
        host_pid=301,
        child_pid=124,
        boundary="container",
        extra={"remote_authority_v2": True},
    )
    index = HostIndex(index_path)
    index.register(initial)
    index.register(other)

    state = {"running": True, "pid": 100}

    def stop():
        print("stopping frontend")
        state["running"] = False

    def start():
        print("starting frontend")
        state["running"] = True
        state["pid"] = 200
        HostIndex(index_path).register(HostRecord(
            session_id=initial.session_id,
            port=initial.port,
            host_pid=initial.host_pid,
            child_pid=initial.child_pid,
            boundary=initial.boundary,
            extra={
                "remote_authority_v2": True,
                "recovered_from_remote": True,
            },
        ))

    monkeypatch.setattr("agent_bridge.config.config_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_service_stop", stop)
    monkeypatch.setattr(cli, "_service_start", start)
    monkeypatch.setattr(cli, "_service_is_running", lambda: state["running"])
    monkeypatch.setattr(cli, "_read_pid_file", lambda: state["pid"])

    result = cli._fault_frontend_restart_hostindex_loss("session-1", 1)

    assert result["frontend_pid_before"] == 100
    assert result["frontend_pid_after"] == 200
    assert result["initial_host_pid"] == result["recovered_host_pid"] == 300
    assert result["initial_child_pid"] == result["recovered_child_pid"] == 123
    assert result["recovered_from_remote_authority"] is True
    assert HostIndex(index_path).get("other") == other
    assert capsys.readouterr().out == ""


def test_frontend_restart_fault_restores_frontend_when_mutation_fails(
    tmp_path,
    monkeypatch,
):
    index_path = tmp_path / "hosts" / "index.json"
    HostIndex(index_path).register(HostRecord(
        session_id="session-1",
        port=5000,
        host_pid=300,
        child_pid=123,
        boundary="container",
        extra={"remote_authority_v2": True},
    ))
    state = {"running": True, "starts": 0}

    def stop():
        state["running"] = False

    def start():
        state["running"] = True
        state["starts"] += 1

    def fail_remove(_self, _session_id):
        raise OSError("write failed")

    monkeypatch.setattr("agent_bridge.config.config_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_service_stop", stop)
    monkeypatch.setattr(cli, "_service_start", start)
    monkeypatch.setattr(cli, "_service_is_running", lambda: state["running"])
    monkeypatch.setattr(cli, "_read_pid_file", lambda: 100)
    monkeypatch.setattr(HostIndex, "remove", fail_remove)

    with pytest.raises(OSError, match="write failed"):
        cli._fault_frontend_restart_hostindex_loss("session-1", 1)

    assert state == {"running": True, "starts": 1}


def test_relay_interruption_fault_reprobes_credentials_without_duplication():
    client = FakeClient()

    result = parity.run(
        client,
        "container:example-1",
        auth=True,
        ado_url="https://example.visualstudio.com/Project/_git/repo",
        azure_scope="https://storage.azure.com/.default",
        startup_timeout=1,
        turn_timeout=1,
        fault=parity.RELAY_INTERRUPTION,
    )

    assert result.ok is True
    assert result.checks["exclusive_relay_fault"] is True
    assert result.checks["relay_interrupted"] is True
    assert result.checks["relay_recovered"] is True
    assert result.checks["no_duplicate_relay_owner"] is True
    assert result.checks["relay_process_replaced"] is True
    assert result.checks["credential_events_redacted"] is True
    assert result.checks["relay_github_credential"] is True
    assert result.checks["relay_ado_ls_remote"] is True
    assert result.checks["relay_azure_token"] is True
    assert result.checks["same_acp_session"] is True
    assert result.checks["same_child"] is True
    assert result.observed["relay_recovery_probe"]["azure_token"] is True
    assert client.relay_fault_calls == 1
    assert len(client.prompts) == 3


def test_failed_handshake_fault_rolls_back_then_acquires_same_target():
    client = FakeClient()

    result = parity.run(
        client,
        "container:example-1",
        startup_timeout=1,
        turn_timeout=1,
        fault=FAILED_ACP_HANDSHAKE_FAULT,
    )

    assert result.ok is True
    assert result.checks["failed_start_cleanup_confirmed"] is True
    assert result.checks["remote_authority_removed"] is True
    assert result.checks["target_lock_removed"] is True
    assert result.checks["fresh_same_target_acquisition"] is True
    assert result.observed["failed_start_cleanup"][
        "remote_authority_removed"
    ] is True
    assert client.handshake_fault_calls == 1
    assert client.start_calls == 2
    assert client.ended is True


def test_container_recreate_fault_uses_fresh_identity_and_reprobes():
    client = FakeClient()

    result = parity.run(
        client,
        "container:example-1",
        expected_workspace="/workspaces/example",
        expected_capability="local-skill",
        startup_timeout=1,
        turn_timeout=1,
        fault=CONTAINER_RECREATE_FAULT,
    )

    assert result.ok is True
    assert result.session_id == "session-2"
    assert result.initial_host_pid == 300
    assert result.resumed_host_pid == 400
    assert result.resumed_acp_session_id == "acp-2"
    assert result.resumed_child_pid == 456
    assert result.checks["container_identity_changed"] is True
    assert result.checks["fresh_acp_session"] is True
    assert result.checks["fresh_host"] is True
    assert result.checks["fresh_child"] is True
    assert result.checks["replacement_workspace"] is True
    assert result.checks["replacement_capability"] is True
    assert client.container_recreate_calls == 1
    assert len(client.prompts) == 3
    assert client.ended is True


def test_quality_prompt_rejects_credentialed_ado_url():
    with pytest.raises(parity.ParityFailure, match="embedded credentials"):
        parity._quality_prompt(
            auth=True,
            ado_url="https://user:secret@example.visualstudio.com/P/_git/R",
            azure_scope=None,
        )
