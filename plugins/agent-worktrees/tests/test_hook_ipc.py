from __future__ import annotations

import importlib.util
import io
import json
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_worktrees.hook_ipc as hook_ipc
import agent_worktrees.__main__ as main
from agent_worktrees import reconcile
from agent_worktrees.hook_ipc import HookIpcServer, HookUnavailable

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hook_client.py"
_SPEC = importlib.util.spec_from_file_location("hook_client_under_test", _SCRIPT)
assert _SPEC and _SPEC.loader
hook_client = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = hook_client
_SPEC.loader.exec_module(hook_client)


def test_dynamic_loopback_endpoint_roundtrip(tmp_path):
    seen = {}

    def decide(kind, payload, deadline):
        seen.update(kind=kind, payload=payload)
        return {"additionalContext": "resident"}

    server = HookIpcServer(decide)
    server.start()
    try:
        endpoint = server.rendezvous()
        host, port = endpoint["hook_endpoint"].split(":")
        assert host == "127.0.0.1"
        assert int(port) != 0
        result = hook_client._request(
            "preToolUse", {"toolName": "view"}, tmp_path)
        assert result is None

        runtime = tmp_path / ".agent-worktrees"
        runtime.mkdir(parents=True)
        (runtime / "status-monitor.lock").write_text(
            json.dumps(endpoint), encoding="utf-8")
        result = hook_client._request(
            "preToolUse", {"toolName": "view"}, tmp_path)
        assert result == {"additionalContext": "resident"}
        assert seen == {
            "kind": "preToolUse",
            "payload": {"toolName": "view"},
        }
    finally:
        server.close()


def test_bad_token_gets_no_response(tmp_path):
    server = HookIpcServer(lambda kind, payload, deadline: {})
    server.start()
    try:
        endpoint = server.rendezvous()
        host, port = endpoint["hook_endpoint"].split(":")
        with socket.create_connection((host, int(port)), timeout=1) as conn:
            conn.sendall(
                json.dumps({
                    "version": 1,
                    "token": "wrong",
                    "kind": "preToolUse",
                    "payload": {},
                    "deadline": time.time() + 1,
                }).encode() + b"\n"
            )
            assert conn.recv(100) == b""
    finally:
        server.close()


def test_stalled_connection_is_closed_after_read_timeout():
    server = HookIpcServer(lambda kind, payload, deadline: {})
    server.start()
    try:
        endpoint = server.rendezvous()
        host, port = endpoint["hook_endpoint"].split(":")
        with socket.create_connection((host, int(port)), timeout=1) as conn:
            conn.settimeout(2)
            assert conn.recv(100) == b""
    finally:
        server.close()


def test_fallback_write_ignores_disconnected_client():
    class DisconnectedWriter:
        def write(self, data):
            raise BrokenPipeError

    handler = hook_ipc._Handler.__new__(hook_ipc._Handler)
    handler.request = SimpleNamespace(settimeout=lambda timeout: None)
    handler.rfile = io.BytesIO(
        json.dumps({
            "version": 1,
            "token": "token",
            "kind": "preToolUse",
            "payload": {},
            "deadline": time.time() - 1,
        }).encode() + b"\n"
    )
    handler.wfile = DisconnectedWriter()
    handler.server = SimpleNamespace(token="token", decide=lambda *args: {})
    handler.handle()


def test_busy_server_explicitly_requests_fallback(tmp_path):
    def unavailable(kind, payload, deadline):
        raise HookUnavailable

    server = HookIpcServer(unavailable)
    server.start()
    try:
        runtime = tmp_path / ".agent-worktrees"
        runtime.mkdir(parents=True)
        (runtime / "status-monitor.lock").write_text(
            json.dumps(server.rendezvous()), encoding="utf-8")
        assert hook_client._request(
            "postToolUse", {"toolName": "view"}, tmp_path) is None
    finally:
        server.close()


def test_client_falls_back_to_pre_guards(monkeypatch, tmp_path):
    class Guard:
        @staticmethod
        def decide(payload, home):
            return {"permissionDecision": "deny"}

    monkeypatch.setattr(hook_client, "_request", lambda *a, **k: None)
    monkeypatch.setattr(hook_client, "_load_sibling", lambda name: Guard)
    assert hook_client.decide(
        "preToolUse", {"toolName": "edit"}, home=tmp_path
    ) == {"permissionDecision": "deny"}


def test_advisory_guard_does_not_suppress_later_deny(monkeypatch, tmp_path):
    class Allow:
        @staticmethod
        def decide(payload, home):
            return None

    class Warn:
        @staticmethod
        def decide(payload, home, deadline=None):
            return {"additionalContext": "route elsewhere"}

    class Deny:
        @staticmethod
        def decide(payload, home):
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": "anchor protected",
            }

    modules = iter((Allow, Warn, Deny))
    monkeypatch.setattr(hook_client, "_request", lambda *a, **k: None)
    monkeypatch.setattr(hook_client, "_load_sibling", lambda name: next(modules))
    result = hook_client.decide(
        "preToolUse", {"toolName": "edit"}, home=tmp_path)
    assert result["permissionDecision"] == "deny"
    assert result["permissionDecisionReason"] == "anchor protected"
    assert result["additionalContext"] == "route elsewhere"


def test_client_does_not_import_agent_worktrees_main():
    text = _SCRIPT.read_text("utf-8")
    assert "agent_worktrees.__main__" not in text
    assert '"agent_worktrees",' in text
    assert '"session-lifecycle"' in text


def test_session_start_fallback_uses_one_runtime_process(monkeypatch, tmp_path):
    runtime = tmp_path / ".agent-worktrees"
    python = runtime / "versions" / "1.2.3" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (python.parents[1] / ".install-complete.json").write_text(
        json.dumps({
            "version": "1.2.3",
            "completed_at": "2026-01-01T00:00:00Z",
            "pid": 1,
        }),
        encoding="utf-8",
    )
    (runtime / "current-version").write_text("1.2.3", encoding="utf-8")
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(
            stdout='{"additionalContext":"fallback"}',
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(hook_client.subprocess, "run", run)
    result = hook_client._fallback_session_start(
        {"sessionId": "session-1", "cwd": str(tmp_path)}, tmp_path
    )
    assert result == {"additionalContext": "fallback"}
    assert seen["argv"] == [
        str(python),
        "-m",
        "agent_worktrees",
        "session-lifecycle",
        "--stdin",
        "--timeout-seconds",
        "10.0",
    ]
    assert seen["kwargs"]["env"]["PYTHONPATH"] == ""


def test_session_start_rejects_old_resident_without_lifecycle_capability(
    monkeypatch, tmp_path
):
    runtime = tmp_path / ".agent-worktrees"
    runtime.mkdir(parents=True)
    (runtime / "status-monitor.lock").write_text(
        json.dumps({
            "hook_transport": "tcp",
            "hook_endpoint": "127.0.0.1:1234",
            "hook_token": "token",
        }),
        encoding="utf-8",
    )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def settimeout(self, timeout):
            pass

        def sendall(self, request):
            pass

        def recv(self, size):
            return b'{"version":1,"result":{}}\n'

    monkeypatch.setattr(
        hook_client.socket,
        "create_connection",
        lambda *args, **kwargs: Connection(),
    )
    assert hook_client._request("sessionStart", {}, tmp_path) is None


def test_session_start_old_runtime_uses_legacy_compatibility(
    monkeypatch, tmp_path
):
    runtime = tmp_path / ".agent-worktrees"
    python = runtime / "versions" / "1.5.3-dev744" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (python.parents[1] / ".install-complete.json").write_text(
        json.dumps({"version": "1.5.3-dev744"}),
        encoding="utf-8",
    )
    (runtime / "current-version").write_text(
        "1.5.3-dev744", encoding="utf-8"
    )
    seen = {}

    def legacy(payload):
        seen.update(payload)
        return {"additionalContext": "legacy"}

    monkeypatch.setattr(
        hook_client, "_fallback_legacy_session_start", legacy
    )
    monkeypatch.setattr(
        hook_client.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("old runtime must not be invoked"),
    )
    payload = {
        "sessionId": "session-1",
        "cwd": str(tmp_path),
        "_agentWorktrees": {
            "pluginVersion": "1.5.3-dev745",
            "environment": {},
        },
    }
    assert hook_client._fallback_session_start(payload, tmp_path) == {
        "additionalContext": "legacy"
    }
    assert seen["sessionId"] == "session-1"


def test_session_start_enriches_payload_with_session_environment(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(hook_client, "_plugin_version", lambda: "1.2.3-dev4")
    monkeypatch.setenv("WORKTREE_ID", "worktree-1")
    monkeypatch.setenv("TMUX_PANE", "%7")
    monkeypatch.setenv("AGENT_WORKTREES_HANDOFF_TOKEN", "handoff-1")
    monkeypatch.setenv("WORKTREE_NO_RECONCILE", "1")
    monkeypatch.setenv("WORKTREE_NO_PROVISION", "1")
    monkeypatch.setenv("CUSTOM_SESSION_VALUE", "current-session")
    enriched = hook_client._enrich_session_payload({
        "sessionId": "session-1",
        "cwd": str(tmp_path),
    })
    metadata = enriched["_agentWorktrees"]
    assert metadata["pluginVersion"] == "1.2.3-dev4"
    assert "WORKTREE_ID" not in metadata["environment"]
    assert metadata["environment"]["TMUX_PANE"] == "%7"
    assert (
        metadata["environment"]["AGENT_WORKTREES_HANDOFF_TOKEN"]
        == "handoff-1"
    )
    assert metadata["environment"]["WORKTREE_NO_RECONCILE"] == "1"
    assert metadata["environment"]["WORKTREE_NO_PROVISION"] == "1"
    assert metadata["environment"]["CUSTOM_SESSION_VALUE"] == "current-session"


def test_session_start_writes_combined_session_guidance(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(hook_client, "_plugin_version", lambda: "1.2.3-dev4")
    monkeypatch.setattr(
        hook_client,
        "_command_catalog_context",
        lambda: "## command catalog\n\ncatalog",
    )
    payload = {
        "sessionId": "session-1",
        "cwd": str(tmp_path),
        "source": "new",
        "timestamp": 1_000,
    }
    enriched = hook_client._enrich_session_payload(payload)
    launch_key = hook_client._session_launch_key(enriched)
    snapshots = tmp_path / ".agent-worktrees" / ".session-context"
    snapshots.mkdir(parents=True)
    (snapshots / f"register-session-{launch_key}.json").write_text(
        json.dumps({
            "launchKey": launch_key,
            "output": json.dumps({
                "additionalContext": "[agent-worktrees] binding"
            }),
        }),
        encoding="utf-8",
    )

    assert hook_client._write_session_guidance(payload, home=tmp_path)
    target = (
        tmp_path
        / ".copilot"
        / "session-state"
        / "session-1"
        / "instructions"
        / "agent-worktrees"
        / "session-guidance.instructions.md"
    )
    assert target.read_text(encoding="utf-8") == (
        "# Agent Worktrees session guidance\n\n"
        "## command catalog\n\ncatalog\n\n"
        "[agent-worktrees] binding\n"
    )


def test_session_start_guidance_overwrites_on_resume(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(hook_client, "_plugin_version", lambda: "1.2.3-dev4")
    monkeypatch.setattr(
        hook_client, "_command_catalog_context", lambda: "catalog"
    )
    target = (
        tmp_path
        / ".copilot"
        / "session-state"
        / "session-1"
        / "instructions"
        / "agent-worktrees"
        / "session-guidance.instructions.md"
    )
    for timestamp, binding in ((1_000, "first"), (1_001, "second")):
        payload = {
            "sessionId": "session-1",
            "cwd": str(tmp_path),
            "source": "resume",
            "timestamp": timestamp,
        }
        enriched = hook_client._enrich_session_payload(payload)
        launch_key = hook_client._session_launch_key(enriched)
        snapshots = tmp_path / ".agent-worktrees" / ".session-context"
        snapshots.mkdir(parents=True, exist_ok=True)
        (snapshots / f"register-session-{launch_key}.json").write_text(
            json.dumps({
                "launchKey": launch_key,
                "output": json.dumps({"additionalContext": binding}),
            }),
            encoding="utf-8",
        )
        assert hook_client._write_session_guidance(payload, home=tmp_path)

    assert target.read_text(encoding="utf-8").endswith(
        "catalog\n\nsecond\n"
    )


@pytest.mark.parametrize("suffix", (".json", ""))
def test_registration_context_rejects_oversized_snapshots(
    monkeypatch, tmp_path, suffix
):
    monkeypatch.setattr(hook_client, "_plugin_version", lambda: "1.2.3-dev4")
    payload = {
        "sessionId": "session-1",
        "cwd": str(tmp_path),
        "source": "new",
        "timestamp": 1_000,
    }
    enriched = hook_client._enrich_session_payload(payload)
    launch_key = hook_client._session_launch_key(enriched)
    snapshots = tmp_path / ".agent-worktrees" / ".session-context"
    snapshots.mkdir(parents=True)
    snapshot = snapshots / f"register-session-{launch_key}{suffix}"
    snapshot.write_bytes(b"x" * (hook_client._MAX_RESPONSE + 1))

    assert hook_client._registration_context(enriched, tmp_path) == ""


@pytest.mark.parametrize(
    "session_id",
    ("", "../escape", "slash/value", "a" * 129),
)
def test_session_start_guidance_rejects_unsafe_session_ids(
    monkeypatch, tmp_path, session_id
):
    monkeypatch.setattr(
        hook_client, "_command_catalog_context", lambda: "catalog"
    )
    assert not hook_client._write_session_guidance(
        {"sessionId": session_id}, home=tmp_path
    )
    assert not (tmp_path / ".copilot").exists()


def test_session_start_guidance_rejects_instruction_directory_escape(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        hook_client, "_command_catalog_context", lambda: "catalog"
    )
    session_root = (
        tmp_path / ".copilot" / "session-state" / "session-1"
    )
    session_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (session_root / "instructions").symlink_to(
            outside, target_is_directory=True
        )
    except OSError:
        pytest.skip("directory symlinks are not available")

    assert not hook_client._write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )
    assert not (outside / "agent-worktrees").exists()


def test_session_start_guidance_rejects_session_root_escape(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        hook_client, "_command_catalog_context", lambda: "catalog"
    )
    state_root = tmp_path / ".copilot" / "session-state"
    state_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (state_root / "session-1").symlink_to(
            outside, target_is_directory=True
        )
    except OSError:
        pytest.skip("directory symlinks are not available")

    assert not hook_client._write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )
    assert not (outside / "instructions").exists()


@pytest.mark.parametrize("component", (".copilot", "session-state"))
def test_session_start_guidance_rejects_state_root_escape(
    monkeypatch, tmp_path, component
):
    monkeypatch.setattr(
        hook_client, "_command_catalog_context", lambda: "catalog"
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    if component == ".copilot":
        link = tmp_path / ".copilot"
    else:
        copilot_root = tmp_path / ".copilot"
        copilot_root.mkdir()
        link = copilot_root / "session-state"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available")

    assert not hook_client._write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )
    assert not (outside / "session-1").exists()


def test_reparse_detection_does_not_require_creating_a_link():
    path = SimpleNamespace(
        lstat=lambda: SimpleNamespace(
            st_mode=hook_client.stat.S_IFDIR,
            st_file_attributes=hook_client.stat.FILE_ATTRIBUTE_REPARSE_POINT,
        )
    )
    assert hook_client._is_link_or_reparse(path)


def test_session_start_main_writes_guidance_before_emitting_result(
    monkeypatch, capsys
):
    payload = {"sessionId": "session-1", "cwd": str(Path.cwd())}
    seen = {}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(
        hook_client,
        "decide",
        lambda kind, value: {"additionalContext": "supplement"},
    )
    monkeypatch.setattr(
        hook_client,
        "_write_session_guidance",
        lambda value: seen.update(value) or True,
    )

    assert hook_client.main(["sessionStart"]) == 0
    assert seen == payload
    assert json.loads(capsys.readouterr().out) == {
        "additionalContext": "supplement"
    }


def test_first_install_preserves_bootstrap_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        hook_client.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="[agent-worktrees] Runtime not installed.",
            stderr="bootstrap warning\n",
        ),
    )
    monkeypatch.setattr(
        hook_client.shutil,
        "which",
        lambda name: str(tmp_path / name),
    )

    result = hook_client._bootstrap_first_install({"sessionId": "session-1"})

    assert result == {
        "additionalContext": "[agent-worktrees] Runtime not installed.",
        "_stderr": "bootstrap warning\n",
    }


def test_first_install_preserves_non_object_json_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        hook_client.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout='"runtime not installed"',
            stderr="",
        ),
    )
    monkeypatch.setattr(
        hook_client.shutil,
        "which",
        lambda name: str(tmp_path / name),
    )

    result = hook_client._bootstrap_first_install({"sessionId": "session-1"})

    assert result == {"additionalContext": '"runtime not installed"'}


def test_project_hook_uses_exact_current_session_environment(
    monkeypatch, tmp_path
):
    project_dir = tmp_path / "project"
    hooks = project_dir / "hooks"
    hooks.mkdir(parents=True)
    script = hooks / (
        "session-start.ps1" if sys.platform == "win32"
        else "session-start.sh"
    )
    script.write_text("", encoding="utf-8")
    seen = {}

    monkeypatch.setattr(main, "_activate_project_for_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(main.cfg, "project_name", lambda: "example")
    monkeypatch.setattr(main.cfg, "project_dir", lambda project: project_dir)
    monkeypatch.setattr(main.shutil, "which", lambda shell: f"/bin/{shell}")

    def popen(argv, **kwargs):
        seen.update(argv=argv, kwargs=kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(main.subprocess, "Popen", popen)
    environment = {
        "PATH": "current-path",
        "CUSTOM_SESSION_VALUE": "current-session",
    }
    assert main._start_project_session_hook(
        str(tmp_path), environment
    ) is not None
    assert seen["kwargs"]["env"] == environment


def test_windows_session_hook_rejects_windowsapps_python_alias():
    hooks = json.loads(
        (Path(__file__).resolve().parents[1] / "hooks.json").read_text("utf-8")
    )
    command = hooks["hooks"]["sessionStart"][1]["powershell"]
    assert "WindowsApps" in command
    assert "$ran = ($LASTEXITCODE -eq 0)" in command
    bash = hooks["hooks"]["sessionStart"][1]["bash"]
    assert 'if python3 "$s" sessionStart; then ran=true; fi' in bash


def test_started_resident_lifecycle_suppresses_duplicate_fallback(
    monkeypatch, tmp_path
):
    payload = {
        "sessionId": "session-1",
        "cwd": str(tmp_path),
        "source": "new",
        "timestamp": 1_000,
        "_agentWorktrees": {
            "pluginVersion": "1.2.3-dev4",
            "environment": {},
        },
    }
    launch_key = hook_client._session_launch_key(payload)
    receipts = tmp_path / ".agent-worktrees" / ".session-context"
    receipts.mkdir(parents=True)
    (receipts / f"lifecycle-{launch_key}.json").write_text(
        json.dumps({"launchKey": launch_key, "state": "started"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(hook_client, "_request", lambda *args: None)
    monkeypatch.setattr(
        hook_client, "_enrich_session_payload", lambda original: payload
    )
    monkeypatch.setattr(
        hook_client,
        "_fallback_session_start",
        lambda *args: pytest.fail("duplicate lifecycle fallback"),
    )
    assert hook_client.decide(
        "sessionStart",
        {"sessionId": "session-1", "cwd": str(tmp_path)},
        home=tmp_path,
    ) == {}


def test_resident_dispatches_session_start_to_combined_lifecycle(monkeypatch):
    seen = {}
    provisioning_start_event = threading.Event()

    def lifecycle(
        payload,
        *,
        deadline=None,
        provisioning_start_event=None,
        plugin_related_anchors=None,
    ):
        seen.update(
            payload=payload,
            deadline=deadline,
            provisioning_start_event=provisioning_start_event,
            plugin_related_anchors=plugin_related_anchors,
        )
        return {"additionalContext": "project hook"}

    monkeypatch.setattr(main, "_run_session_lifecycle", lifecycle)
    result = main._resident_hook_decision(
        "sessionStart",
        {"cwd": str(Path.cwd()), "sessionId": "session-1"},
        segment_cache=SimpleNamespace(),
        policy=SimpleNamespace(
            plugin_related_anchors=lambda: ["plugin-anchor"]
        ),
        deadline=123.0,
        provisioning_start_event=provisioning_start_event,
    )
    assert result == {"additionalContext": "project hook"}
    assert seen["payload"]["sessionId"] == "session-1"
    assert seen["deadline"] == 123.0
    assert seen["provisioning_start_event"] is provisioning_start_event
    assert seen["plugin_related_anchors"] == ["plugin-anchor"]


def test_resident_policy_caches_plugin_related_anchors(monkeypatch):
    from agent_worktrees import related

    calls = []
    monkeypatch.setattr(
        related,
        "installed_plugin_related_anchors",
        lambda: calls.append("discover") or ["plugin-anchor"],
    )
    policy = main._ResidentHookPolicy(None)

    assert policy.plugin_related_anchors() == ["plugin-anchor"]
    assert policy.plugin_related_anchors() == ["plugin-anchor"]
    assert calls == ["discover"]


def test_combined_lifecycle_preserves_side_effect_snapshots(monkeypatch, tmp_path):
    payload = {
        "sessionId": "session-1",
        "workingDirectory": str(tmp_path),
        "source": "new",
        "timestamp": 1_000,
        "_agentWorktrees": {
            "pluginVersion": "1.5.3-dev745",
            "environment": {
                "WORKTREE_ID": "worktree-1",
                "TMUX_PANE": "%7",
                "WORKTREE_LAUNCH_ID": "launch-1",
                "AGENT_WORKTREES_PROFILE_ASSIGNMENT_TOKEN": "assignment-1",
                "AGENT_WORKTREES_HANDOFF_TOKEN": "handoff-1",
            },
        },
    }
    snapshots = []
    restored = []

    monkeypatch.setattr(main.cfg, "active_project", lambda: "before")
    monkeypatch.setattr(
        main.cfg, "set_active_project", lambda value: restored.append(value)
    )
    monkeypatch.setattr(main, "_start_project_session_hook", lambda cwd, env: "p")
    monkeypatch.setattr(
        main,
        "_finish_project_session_hook",
        lambda process, deadline: (
            {"additionalContext": "project"},
            "project warning\n",
        ),
    )
    monkeypatch.setattr(
        main, "_registration_nudge_context", lambda cwd: "register this repo"
    )
    monkeypatch.setattr(
        main,
        "_write_session_lifecycle_snapshot",
        lambda name, payload, output: snapshots.append((name, output)),
    )
    monkeypatch.setattr(
        main, "_write_session_lifecycle_receipt", lambda payload, state: None
    )
    registration = {}

    def register(args):
        registration.update(vars(args))
        args.result_holder.append('{"additionalContext":"binding"}')
        return 0

    monkeypatch.setattr(main, "cmd_register_session", register)
    monkeypatch.setattr(
        main, "_anchor_hygiene_diagnostic", lambda cwd: "anchor warning\n"
    )
    monkeypatch.setattr(
        main,
        "_reconcile_marketplace_snapshot",
        lambda payload, cwd: snapshots.append(
            ("marketplace-overrides", "{}")
        ),
    )
    monkeypatch.setattr(
        main,
        "_start_provisioning_if_needed",
        lambda cwd, environment: "provisioning\n",
    )

    result = main._run_session_lifecycle(
        payload, deadline=time.time() + 200.0
    )

    assert result["additionalContext"] == "project"
    assert result["_stderr"] == (
        "anchor warning\nprovisioning\nproject warning\n"
    )
    assert snapshots == [
        ("register-nudge", '{"additionalContext": "register this repo"}'),
        ("marketplace-overrides", "{}"),
        ("register-session", '{"additionalContext":"binding"}'),
    ]
    assert restored == ["before"]
    assert registration["worktree_id"] is None
    assert registration["cwd"] == str(tmp_path)
    assert registration["pane"] == "%7"
    assert registration["launch_id"] == "launch-1"
    assert registration["assignment_token"] == "assignment-1"
    assert registration["handoff_candidate_token"] == "handoff-1"
    assert registration["resident_environment"] is True


@pytest.mark.parametrize(
    "flag", ["WORKTREE_NO_RECONCILE", "WORKTREE_NO_PROVISION"]
)
def test_session_provisioning_honors_current_session_opt_out(
    monkeypatch, tmp_path, flag
):
    monkeypatch.setattr(
        main,
        "_aw_runtime_home",
        lambda: tmp_path / ".agent-worktrees",
    )
    monkeypatch.setattr(
        reconcile,
        "build_plan",
        lambda *args, **kwargs: pytest.fail("provisioning must be skipped"),
    )
    assert main._start_provisioning_if_needed(
        str(tmp_path), {flag: "1"}
    ) == ""


def test_session_provisioning_ignores_stale_resident_opt_out(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("WORKTREE_NO_PROVISION", "1")
    monkeypatch.setattr(
        main,
        "_aw_runtime_home",
        lambda: tmp_path / ".agent-worktrees",
    )
    seen = {}

    def build_plan(repo, *, save):
        seen.update(repo=repo, save=save)
        return {"action": "none"}

    monkeypatch.setattr(reconcile, "build_plan", build_plan)
    assert main._start_provisioning_if_needed(str(tmp_path), {}) == ""
    assert seen == {"repo": tmp_path, "save": False}


def test_session_lifecycle_schedules_provisioning_without_waiting(
    monkeypatch, tmp_path
):
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    lifecycle_released = threading.Event()
    monkeypatch.setattr(
        main,
        "_provisioning_status_diagnostic",
        lambda cwd: "",
    )

    def provision(
        cwd,
        environment,
        *,
        include_status_diagnostic,
        process_holder,
    ):
        started.set()
        release.wait(5)
        completed.set()
        return ""

    monkeypatch.setattr(main, "_start_provisioning_if_needed", provision)

    before = time.perf_counter()
    assert main._schedule_provisioning_if_needed(
        str(tmp_path), {}, start_event=lifecycle_released
    ) == ""
    elapsed = time.perf_counter() - before

    assert not started.wait(0.05)
    assert elapsed < 0.5
    lifecycle_released.set()
    assert started.wait(1)
    release.set()
    assert completed.wait(1)


def test_session_lifecycle_deduplicates_provisioning_preview(
    monkeypatch, tmp_path
):
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    calls = []
    monkeypatch.setattr(
        main,
        "_provisioning_status_diagnostic",
        lambda cwd: "",
    )

    class Process:
        def wait(self):
            release.wait(5)
            completed.set()

    def provision(
        cwd,
        environment,
        *,
        include_status_diagnostic,
        process_holder,
    ):
        calls.append(cwd)
        process_holder.append(Process())
        started.set()
        return ""

    monkeypatch.setattr(main, "_start_provisioning_if_needed", provision)

    assert main._schedule_provisioning_if_needed(str(tmp_path), {}) == ""
    assert started.wait(1)
    assert main._schedule_provisioning_if_needed(str(tmp_path), {}) == ""
    assert calls == [str(tmp_path)]
    release.set()
    assert completed.wait(1)


def test_late_completed_request_does_not_request_duplicate_fallback(tmp_path):
    def decide(kind, payload, deadline):
        time.sleep(0.55)
        return {"additionalContext": "completed"}

    server = HookIpcServer(decide)
    server.start()
    try:
        endpoint = server.rendezvous()
        host, port = endpoint["hook_endpoint"].split(":")
        with socket.create_connection((host, int(port)), timeout=1) as conn:
            conn.sendall(
                json.dumps({
                    "version": 1,
                    "token": endpoint["hook_token"],
                    "kind": "sessionStart",
                    "payload": {},
                    "deadline": time.time() + 0.5,
                }).encode() + b"\n"
            )
            response = json.loads(conn.makefile().readline())
        assert response["result"] == {"additionalContext": "completed"}
        assert "fallback" not in response
    finally:
        server.close()
