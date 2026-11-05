"""Provider launch recovery keeps one native identity and one owned admission."""

import copy
import json
from types import SimpleNamespace

import pytest

from agent_codespaces import portable_mux_launch as launch
from agent_codespaces import copilot_detach as detach


@pytest.fixture
def flow(monkeypatch):
    context = json.dumps({
        "schema": "agent-bridge.receiving-policy", "version": 1, "marketplaceId": "",
    })
    state = SimpleNamespace(
        pending=None, meta=None, launch_calls=[], sends=[], observed=0,
        native=None, live={}, unknown=False, registration_missing=False, changes=False,
        releases=[], reservation_releases=[],
    )

    class Bridge:
        policy_context = context
        capabilities = {"daemon_port": 12001}

        def run(self, *args, **kwargs):
            pytest.fail("unexpected real Bridge subprocess")

        def launch(self, request):
            state.launch_calls.append(copy.deepcopy(request))
            return "selected-receiver", json.dumps({
                "request": {**request, "policy_context": context},
                "support": "changed" if state.changes else "selected-assets",
            }).encode()

    monkeypatch.setattr(launch, "BridgeReceiving", Bridge)
    monkeypatch.setattr(launch, "_probe", lambda *a: "/home/fixture/.agent-bridge/receiving")
    monkeypatch.setattr(launch, "require_owner", lambda: None)
    monkeypatch.setattr(launch, "model_copilot_args", lambda args: [])
    monkeypatch.setattr(launch, "live_session_for", lambda *a, **k: state.live)
    monkeypatch.setattr(launch, "reserve_with_retry", lambda *a, **k: {"reservation_id": "original"})
    monkeypatch.setattr(
        launch, "release_cli_mode", lambda *a, **k: state.reservation_releases.append(k["reservation_id"]),
    )
    monkeypatch.setattr(
        launch, "await_claim", lambda *a, **k: None if state.registration_missing else state.native["session_id"],
    )
    monkeypatch.setattr("agent_codespaces.copilot_venue.claim_or_exit_code", lambda *a, **k: None)

    async def forward(*a, **k):
        return True

    monkeypatch.setattr("agent_codespaces.session_forwards.await_owner_bridge_forward", forward)
    monkeypatch.setattr(launch.owner, "ensure_owner_running", lambda config: True)
    monkeypatch.setattr(launch.owner, "get_pending_delivery", lambda *a: copy.deepcopy(state.pending))
    monkeypatch.setattr(
        launch.owner, "get_hold",
        lambda *a: SimpleNamespace(sessions={"cli:scope": state.meta}) if state.meta else None,
    )

    def hold(name, tenant, **kwargs):
        state.meta = {
            "mux_session": kwargs["mux_session"], "mux_endpoint": kwargs["mux_endpoint"],
            "confirmed": kwargs.get("confirmed", False),
        }

    monkeypatch.setattr(launch.owner, "hold", hold)
    monkeypatch.setattr(launch.owner, "record_pending_delivery", lambda name, tenant, value: setattr(state, "pending", copy.deepcopy(value)))
    monkeypatch.setattr(launch.owner, "release", lambda *a: state.releases.append(a))

    def deliver(pending, **kwargs):
        state.sends.append(pending["idempotency_key"])
        return launch.owned_result(
            True, session_id=pending["session_id"], operation=pending["operation"],
            note=pending["envelope"]["prompt"], context=pending["envelope"]["plugin_context"],
        )

    monkeypatch.setattr(launch, "reconcile_owned_delivery", deliver)

    def ssh(args, *, remote_cmd_builder, remote_input_builder, receiving_context, result_sink, settle_on_disconnect):
        assert receiving_context == context
        assert settle_on_disconnect is False
        args.plugin_prepared_context["plugin_context"] = {
            "version": 1, "cwd": "/workspace/product", "plugins": [],
        }
        command = remote_cmd_builder([])
        if command == "selected-receiver":
            wire = remote_input_builder()
            request = json.loads(wire)["request"]
            assert state.pending["phase"] == "submitted"
            assert state.pending["request"]["attempt"] == request["attempt"]
            if state.native is None:
                state.native = {
                    **state.pending["endpoint"], "native": {"incarnation": "$1:1234", "pane": "%2"},
                    "socket_identity": {"device": 1, "inode": 2},
                }
            assert state.native["attempt"] == request["attempt"]
            if state.unknown:
                raise OSError("fixture reply lost after creation")
            return result_sink(SimpleNamespace(exit_code=0, stdout=json.dumps({**state.native, "ok": True, "reused": False})))
        state.observed += 1
        assert remote_input_builder() == b""
        return result_sink(SimpleNamespace(exit_code=0, stdout="present\n"))

    state.ssh = ssh
    state.args = SimpleNamespace(
        name="cs-fixture", worktree_id=None, driver=None, copilot_args=[], seed="one task",
        seed_file=None, ensure_mux=False, register_timeout=0.1, no_relay=False,
    )
    state.plan = {
        "codespace": "cs-fixture", "tenant": "cli:scope", "scope_id": "scope",
        "workspace_folder": "/workspace/product", "identity": "anchor-product", "anchor": True,
        "mux_session": "legacy", "venue": {"kind": "codespace", "target": "cs-fixture"},
    }

    def invoke():
        return launch.cmd_launch(
            state.args, copy.deepcopy(state.plan), object(), SimpleNamespace(state="Available"),
            ssh_session=state.ssh,
        )

    state.invoke = invoke
    return state


def test_complete_launch_send_and_rejoin_keep_the_same_native_conversation(flow, capsys):
    assert flow.invoke() == 0
    first = json.loads(capsys.readouterr().out)
    assert first["seeded"] is True
    assert flow.pending is None
    assert len(flow.sends) == 1
    flow.live = {
        "status": "live", "session_id": flow.native["session_id"],
        "venue": first["venue"],
    }
    flow.args.seed = None
    assert flow.invoke() == 0
    second = json.loads(capsys.readouterr().out)
    assert second["session_id"] == first["session_id"]
    assert flow.observed == 1
    assert len(flow.launch_calls) == len(flow.sends) == 1
    assert flow.releases == []


def test_lost_creation_reply_recovers_the_original_attempt_without_reseeding(flow, capsys):
    flow.unknown = True
    assert flow.invoke() == detach._BUSY_EXIT
    capsys.readouterr()
    retained = copy.deepcopy(flow.pending)
    assert retained["phase"] == "submitted"
    assert flow.releases == flow.reservation_releases == flow.sends == []
    flow.unknown = False
    flow.args.seed = None
    assert flow.invoke() == 0
    assert flow.launch_calls[0] == flow.launch_calls[1]
    assert flow.native["session_id"] == retained["endpoint"]["session_id"]
    assert len(flow.sends) == 1


def test_registration_timeout_retains_attempt_then_recovers_without_replacement(flow, capsys):
    flow.registration_missing = True
    assert flow.invoke() == detach._BUSY_EXIT
    capsys.readouterr()
    native = copy.deepcopy(flow.native)
    assert flow.pending["phase"] == "created"
    assert flow.releases == flow.reservation_releases == []
    flow.args.seed = None
    flow.registration_missing = False
    assert flow.invoke() == 0
    assert flow.native == native
    assert len(flow.sends) == 1


def test_recovery_refuses_changed_receiving_assets_before_remote_effect(flow, capsys):
    flow.unknown = True
    assert flow.invoke() == detach._BUSY_EXIT
    capsys.readouterr()
    retained = copy.deepcopy(flow.pending)
    native = copy.deepcopy(flow.native)
    flow.changes = True
    flow.args.seed = None
    assert flow.invoke() == detach._BUSY_EXIT
    assert flow.pending == retained
    assert flow.native == native
    assert flow.releases == flow.reservation_releases == flow.sends == []


def test_managed_identity_refuses_before_peer_or_claim(flow):
    flow.args.worktree_id = "managed-worktree"
    assert flow.invoke() == 1
    assert flow.meta is flow.pending is None
    assert flow.launch_calls == []


def test_portable_launch_reports_the_current_owner_assigned_forward(flow, monkeypatch, capsys):
    seen = []
    flow.args.local_forwards = ["0:3000"]

    def reported(name, local, prior, assigned):
        seen.append((name, local))
        return {49152: 3000}

    monkeypatch.setattr(detach, "_reported_local_forwards", reported)
    monkeypatch.setattr(detach, "_local_forwards_ready", lambda *a: {49152: True})
    assert flow.invoke() == 0
    result = json.loads(capsys.readouterr().out)
    assert seen == [("cs-fixture", {0: 3000})]
    assert result["local_forwards"] == {"49152": 3000}
    assert result["local_forwards_ready"] == {"49152": True}


def test_failure_before_command_build_keeps_the_original_prepared_request(flow, capsys):
    original_ssh = flow.ssh
    flow.args.copilot_args = ["--model", "fixture"]
    flow.ssh = lambda *a, **k: 1
    assert flow.invoke() == detach._BUSY_EXIT
    capsys.readouterr()
    request = copy.deepcopy(flow.pending["request"])
    assert flow.pending["phase"] == "prepared"
    flow.ssh = original_ssh
    flow.args.seed = None
    flow.args.copilot_args = []
    assert flow.invoke() == 0
    assert flow.launch_calls == [request]


def test_selected_bridge_adapter_keeps_support_payload_on_stdin(monkeypatch):
    from agent_codespaces import bridge_receiving

    policy_context = json.dumps({
        "schema": "agent-bridge.receiving-policy", "version": 1, "marketplaceId": "",
    })
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[-1] == "--owner":
            output = {
                "schema": "agent-bridge.receiving-support", "version": 1, "owner_mux": 1,
                "mux": {"version": 1, "platform": "posix", "transport": "command-stdin", "registration": 1},
                "policy_context": policy_context, "daemon_port": 12001,
            }
        else:
            import base64

            assert json.loads(kwargs["input"]) == {"registration": {"port": 12001}}
            output = {
                "schema": "agent-bridge.receiving-command", "version": 1,
                "command": "receiver", "stdin_base64": base64.b64encode(b"private-support").decode(),
            }
        return SimpleNamespace(returncode=0, stdout=json.dumps(output))

    monkeypatch.setattr(bridge_receiving.worktrees, "validate_context", lambda: None)
    monkeypatch.setattr(bridge_receiving.shutil, "which", lambda name: "/selected/bridge")
    monkeypatch.setattr(bridge_receiving.subprocess, "run", run)
    bridge = bridge_receiving.BridgeReceiving()
    assert bridge.launch({"registration": {"port": 12001}}) == ("receiver", b"private-support")
    assert calls[0][0][0] == calls[1][0][0] == "/selected/bridge"
    assert "12001" not in " ".join(calls[1][0])
