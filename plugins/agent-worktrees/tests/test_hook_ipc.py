from __future__ import annotations

import importlib.util
import io
import json
import os
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


def test_close_admission_rejects_new_connections_with_reason_but_keeps_listening():
    """Single-shot-caller admission discipline: once ``close_admission`` is
    called, the socket stays open and a new connection still completes a
    round trip -- it just gets a structured ``{"fallback": true, "reason":
    ...}`` instead of ``decide`` ever running, distinguishing this from a
    bare OS-level connection-refused a caller cannot act on."""
    server = HookIpcServer(lambda kind, payload, deadline: {"should": "not-run"})
    server.start()
    try:
        endpoint = server.rendezvous()
        host, port = endpoint["hook_endpoint"].split(":")
        server.close_admission("superseded")
        with socket.create_connection((host, int(port)), timeout=1) as conn:
            conn.sendall(
                json.dumps({
                    "version": 1,
                    "token": endpoint["hook_token"],
                    "kind": "preToolUse",
                    "payload": {},
                    "deadline": time.time() + 1,
                }).encode() + b"\n"
            )
            conn.shutdown(socket.SHUT_WR)
            raw = conn.recv(4096)
        response = json.loads(raw.decode("utf-8"))
        assert response == {"version": 1, "fallback": True, "reason": "superseded"}
    finally:
        server.close()


def test_open_admission_reverses_close_admission_on_a_still_live_server():
    seen = {}

    def decide(kind, payload, deadline):
        seen["ran"] = True
        return {"ok": True}

    server = HookIpcServer(decide)
    server.start()
    try:
        endpoint = server.rendezvous()
        host, port = endpoint["hook_endpoint"].split(":")
        server.close_admission("superseded")
        server.open_admission()
        with socket.create_connection((host, int(port)), timeout=1) as conn:
            conn.sendall(
                json.dumps({
                    "version": 1,
                    "token": endpoint["hook_token"],
                    "kind": "preToolUse",
                    "payload": {},
                    "deadline": time.time() + 1,
                }).encode() + b"\n"
            )
            conn.shutdown(socket.SHUT_WR)
            raw = conn.recv(4096)
        response = json.loads(raw.decode("utf-8"))
        assert response.get("result") == {"ok": True}
        assert seen.get("ran") is True
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


@pytest.mark.parametrize("installed", [False, True])
def test_session_start_old_runtime_uses_legacy_compatibility(
    monkeypatch, tmp_path, installed
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
    # An installed but idle Manager must not suppress stale-runtime recovery.
    monkeypatch.setattr(
        hook_client.shutil, "which",
        lambda name: str(tmp_path / "manager-stub") if installed else None,
    )
    manager = tmp_path / "manager"
    manager.mkdir()
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(manager))
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


def test_session_start_old_runtime_skips_legacy_when_worktree_manager_active(
    monkeypatch, tmp_path
):
    """A stale runtime must not trigger this hook's own legacy reconcile
    fallback (which can shell out to a full installer build) when an external
    driver like worktree-manager already owns update/reconcile duties --
    otherwise every session start redundantly races its own install attempt
    against the same runtime slot."""
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
    (runtime / "updater.lock").write_text(
        json.dumps({"pid": os.getpid(), "started": time.time()}),
        encoding="utf-8",
    )
    monkeypatch.setattr(hook_client.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        hook_client,
        "_load_sibling",
        lambda name: SimpleNamespace(
            _pid_alive=lambda pid: pid == os.getpid(),
            _pid_image_path=lambda pid: str(python),
        ),
    )
    seen = {}

    def lifecycle(payload, *, skip_reconcile=False):
        seen["skip_reconcile"] = skip_reconcile
        seen["sessionId"] = payload["sessionId"]
        return {"additionalContext": "registered"}

    monkeypatch.setattr(hook_client, "_fallback_legacy_session_start", lifecycle)
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
        "additionalContext": "registered"
    }
    assert seen == {"skip_reconcile": True, "sessionId": "session-1"}


def test_active_update_preserves_non_reconcile_legacy_hooks(monkeypatch):
    launched = []

    class Process:
        def __init__(self, argv, **kwargs):
            script = next(arg for arg in argv if arg.endswith((".ps1", ".sh")))
            launched.append((Path(script).stem, kwargs["env"]))

        def communicate(self, *args, **kwargs):
            return '{"additionalContext":"lifecycle"}', ""

    monkeypatch.setattr(hook_client.shutil, "which", lambda name: "shell")
    monkeypatch.setattr(hook_client.subprocess, "Popen", Process)
    assert hook_client._fallback_legacy_session_start(
        {"sessionId": "session-1"}, skip_reconcile=True
    ) == {"additionalContext": "lifecycle"}
    assert {name for name, env in launched} == {
        "project-hooks", "register-nudge", "register-session", "anchor-hygiene-check"
    }
    for name, env in launched:
        assert env["WORKTREE_NO_RECONCILE"] == "1"
        assert env["WORKTREE_NO_PROVISION"] == "1"


@pytest.mark.parametrize(
    "ownership",
    [
        None,
        {"pid": 0, "started": 999},
        {"pid": True, "started": 999},
        {"pid": 1, "started": "999"},
        {"pid": 1, "started": float("nan")},
        {"pid": 1, "started": 10 ** 1000},
        {"pid": 1, "started": -(10 ** 1000)},
        {"pid": 1, "started": 1001},
        {"pid": 1, "started": 880},
    ],
)
def test_update_driver_rejects_absent_or_invalid_ownership(
    monkeypatch, tmp_path, ownership
):
    runtime = tmp_path / ".agent-worktrees"
    runtime.mkdir()
    if ownership is not None:
        (runtime / "updater.lock").write_text(json.dumps(ownership), encoding="utf-8")
    (runtime / "updater-status.json").write_text(
        json.dumps({"stage_done": True, "skipped": "locked"}), encoding="utf-8"
    )
    monkeypatch.setattr(hook_client.time, "time", lambda: 1000)
    monkeypatch.setattr(
        hook_client, "_load_sibling",
        lambda name: pytest.fail("invalid ownership must not probe a process"),
    )
    assert not hook_client._external_update_driver_active(tmp_path, Path(sys.executable))


@pytest.mark.parametrize("alive,image", [(False, "matching"), (True, None), (True, "other")])
def test_update_driver_rejects_dead_or_wrong_process(monkeypatch, tmp_path, alive, image):
    runtime = tmp_path / ".agent-worktrees"
    runtime.mkdir()
    (runtime / "updater.lock").write_text(
        json.dumps({"pid": 123, "started": time.time()}), encoding="utf-8"
    )
    python = tmp_path / "python"
    monkeypatch.setattr(
        hook_client, "_load_sibling",
        lambda name: SimpleNamespace(
            _pid_alive=lambda pid: alive,
            _pid_image_path=lambda pid: str(python) if image == "matching" else image,
        ),
    )
    assert not hook_client._external_update_driver_active(tmp_path, python)


def test_update_driver_tracks_real_stage_lock_lifetime(tmp_path):
    from agent_worktrees import update_stage

    runtime = tmp_path / ".agent-worktrees"
    lock = runtime / "updater.lock"
    helper = hook_client._load_sibling("versioned_runtime.py")
    assert helper is not None
    # Test venvs can use a trampoline; production version slots use copied
    # interpreters. Bind this fixture to the actual running image.
    image = helper._pid_image_path(os.getpid())
    assert image is not None
    python = Path(image)
    assert update_stage.acquire_lock(lock)
    try:
        assert hook_client._external_update_driver_active(tmp_path, python)
    finally:
        update_stage.release_lock(lock)
    assert not hook_client._external_update_driver_active(tmp_path, python)


@pytest.mark.parametrize("failure", ["unavailable", "probe-error"])
def test_update_driver_unverified_ownership_preserves_recovery(
    monkeypatch, tmp_path, failure
):
    runtime = tmp_path / ".agent-worktrees"
    runtime.mkdir()
    (runtime / "updater.lock").write_text(
        json.dumps({"pid": 123, "started": time.time()}), encoding="utf-8"
    )

    def probe(pid):
        raise OSError("process probe unavailable")

    helper = None if failure == "unavailable" else SimpleNamespace(_pid_image_path=probe)
    monkeypatch.setattr(hook_client, "_load_sibling", lambda name: helper)
    assert not hook_client._external_update_driver_active(tmp_path, Path(sys.executable))


@pytest.mark.parametrize("result", ["/runtime/bin/python", None, "probe-error"])
def test_update_driver_darwin_native_image_probe(monkeypatch, tmp_path, result):
    import ctypes

    runtime = tmp_path / ".agent-worktrees"
    runtime.mkdir()
    (runtime / "updater.lock").write_text(
        json.dumps({"pid": 123, "started": time.time()}), encoding="utf-8"
    )
    calls = []

    def probe(pid, buffer, size):
        calls.append((pid, size))
        if result == "probe-error":
            raise OSError("native probe unavailable")
        if result is None:
            return 0
        buffer.value = os.fsencode(result)
        return len(buffer.value)

    monkeypatch.setattr(hook_client.sys, "platform", "darwin")
    monkeypatch.setattr(
        hook_client, "_load_sibling",
        lambda name: SimpleNamespace(
            _pid_image_path=lambda pid: None, _pid_alive=lambda pid: True,
        ),
    )
    monkeypatch.setattr(
        ctypes, "CDLL",
        lambda name: SimpleNamespace(proc_pidpath=probe),
    )
    assert hook_client._external_update_driver_active(
        tmp_path, Path("/runtime/bin/python")
    ) is (result == "/runtime/bin/python")
    assert calls == [(123, 4096)]


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


def test_session_start_reuses_registration_with_command_catalog(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        hook_client,
        "_registration_context",
        lambda payload, home: (
            "## agent-worktrees session command catalog\n\n"
            "catalog\n\n[agent-worktrees] binding"
        ),
    )
    monkeypatch.setattr(
        hook_client,
        "_command_catalog_context",
        lambda: pytest.fail("merged registration must avoid a subprocess"),
    )

    assert hook_client._write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
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
    assert target.read_text(encoding="utf-8").endswith(
        "catalog\n\n[agent-worktrees] binding\n"
    )


@pytest.mark.parametrize(
    ("registration", "catalog", "expected"),
    (
        (
            "",
            "",
            "No current agent-worktrees session guidance was available.",
        ),
        (
            "x" * 4096,
            "",
            "Current agent-worktrees session guidance was omitted because",
        ),
    ),
)
def test_session_start_replaces_stale_guidance_on_logical_failure(
    monkeypatch, tmp_path, registration, catalog, expected
):
    monkeypatch.setattr(
        hook_client,
        "_registration_context",
        lambda payload, home: registration,
    )
    monkeypatch.setattr(
        hook_client, "_command_catalog_context", lambda: catalog
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
    target.parent.mkdir(parents=True)
    target.write_text("stale guidance", encoding="utf-8")

    assert hook_client._write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )
    content = target.read_text(encoding="utf-8")
    assert "stale guidance" not in content
    assert expected in content


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


def test_session_start_guidance_fails_open_on_resolve_loop(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        hook_client, "_registration_context", lambda payload, home: ""
    )
    monkeypatch.setattr(
        hook_client, "_command_catalog_context", lambda: "catalog"
    )
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path.name == "session-state":
            raise RuntimeError("symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert not hook_client._write_session_guidance(
        {"sessionId": "session-1"}, home=tmp_path
    )


def test_session_start_main_folds_decision_context_and_emits_empty_result(
    monkeypatch, capsys
):
    payload = {"sessionId": "session-1", "cwd": str(Path.cwd())}
    enriched = {
        **payload,
        "_agentWorktrees": {
            "pluginVersion": "1.2.3-dev4",
            "environment": {},
        },
    }
    seen = {"enrichment_calls": 0}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    def enrich(value):
        seen["enrichment_calls"] += 1
        return enriched

    monkeypatch.setattr(hook_client, "_enrich_session_payload", enrich)
    monkeypatch.setattr(
        hook_client,
        "decide",
        lambda kind, value: (
            seen.update(decide_payload=value)
            or {"additionalContext": "supplement"}
        ),
    )
    monkeypatch.setattr(
        hook_client,
        "_write_session_guidance",
        lambda value, *, decision_context="": (
            seen.update(
                guidance_payload=value, guidance_decision_context=decision_context
            )
            or True
        ),
    )

    assert hook_client.main(["sessionStart"]) == 0
    assert seen["enrichment_calls"] == 1
    assert seen["decide_payload"] is enriched
    assert seen["guidance_payload"] is enriched
    assert seen["guidance_decision_context"] == "supplement"
    assert json.loads(capsys.readouterr().out) == {}


def test_session_start_main_emits_empty_object_on_unhandled_exception(
    monkeypatch, capsys
):
    """sessionStart must emit a single JSON object even if decide() raises.

    main()'s output defaults to "{}" before any risky work runs, so an
    exception anywhere in the try block (decide(), payload enrichment,
    _write_session_guidance) still leaves valid stdout for the host to parse.
    """
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"sessionId": "s"})))
    monkeypatch.setattr(
        hook_client,
        "decide",
        lambda kind, value: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert hook_client.main(["sessionStart"]) == 0
    assert json.loads(capsys.readouterr().out) == {}


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
    command = hooks["hooks"]["sessionStart"][0]["powershell"]
    assert "WindowsApps" in command
    assert "$ran = ($LASTEXITCODE -eq 0)" in command
    assert "-not $env:COPILOT_EXTENSIONS_CONTEXT" in command
    bash = hooks["hooks"]["sessionStart"][0]["bash"]
    assert 'if python3 "$s" sessionStart; then ran=true; fi' in bash
    assert '[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ]' in bash
    assert '${COPILOT_PLUGIN_ROOT:-$PWD}' not in bash


def test_explicit_context_pre_and_post_hooks_use_payload_client_only():
    hooks = json.loads(
        (Path(__file__).resolve().parents[1] / "hooks.json").read_text("utf-8")
    )
    for kind in ("preToolUse", "postToolUse"):
        entry = hooks["hooks"][kind][0]
        assert "COPILOT_EXTENSIONS_CONTEXT" in entry["powershell"]
        assert "COPILOT_PLUGIN_ROOT" in entry["powershell"]
        assert "COPILOT_EXTENSIONS_CONTEXT" in entry["bash"]
        assert "COPILOT_PLUGIN_ROOT" in entry["bash"]
        assert '${COPILOT_PLUGIN_ROOT:-}/scripts' not in entry["bash"]


def test_explicit_context_uses_matching_resident_monitor(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_CONTEXT",
        str(tmp_path / "cell" / "install.json"),
    )
    seen = []
    monkeypatch.setattr(
        hook_client,
        "_request",
        lambda *_args, **_kwargs: seen.append(True) or {"permissionDecision": "allow"},
    )
    monkeypatch.setattr(
        hook_client,
        "_fallback_pre",
        lambda *_args, **_kwargs: pytest.fail("scoped resident should answer first"),
    )

    result = hook_client.decide("preToolUse", {}, home=tmp_path)

    assert result == {"permissionDecision": "allow"}
    assert seen == [True]


def test_explicit_context_request_reads_selected_cell_lock(monkeypatch, tmp_path):
    cell = tmp_path / "cell"
    runtime = tmp_path / ".agent-worktrees"
    runtime.mkdir(parents=True)
    server = HookIpcServer(lambda kind, payload, deadline: {"additionalContext": "cell"})
    server.start()
    try:
        (runtime / "status-monitor.lock").write_text(
            json.dumps(server.rendezvous()),
            encoding="utf-8",
        )
        endpoint = {
            **server.rendezvous(),
            "marketplaceId": "cell-a",
            "installReceipt": str(cell / "install.json"),
            "pluginRoot": str(cell),
        }
        cell.mkdir(parents=True)
        (cell / "status-monitor.lock").write_text(json.dumps(endpoint), encoding="utf-8")
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(cell / "install.json"))

        class RegistryRoot:
            @staticmethod
            def resolve_registry_root(**_kwargs):
                return cell

            @staticmethod
            def resolve_registry_context(**_kwargs):
                return {
                    "marketplaceId": "cell-a",
                    "installReceipt": str(cell / "install.json"),
                    "pluginRoot": str(cell),
                }

        monkeypatch.setattr(
            hook_client,
            "_load_sibling",
            lambda name: RegistryRoot if name == "registry_root.py" else None,
        )

        assert hook_client._request("preToolUse", {}, tmp_path) == {
            "additionalContext": "cell"
        }
    finally:
        server.close()


def test_explicit_context_request_rejects_mismatched_monitor(monkeypatch, tmp_path):
    cell = tmp_path / "cell"
    cell.mkdir(parents=True)
    server = HookIpcServer(lambda kind, payload, deadline: {"additionalContext": "cell"})
    server.start()
    try:
        endpoint = {
            **server.rendezvous(),
            "marketplaceId": "other-cell",
            "installReceipt": str(tmp_path / "other" / "install.json"),
            "pluginRoot": str(tmp_path / "other"),
        }
        (cell / "status-monitor.lock").write_text(json.dumps(endpoint), encoding="utf-8")
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(cell / "install.json"))

        class RegistryRoot:
            @staticmethod
            def resolve_registry_root(**_kwargs):
                return cell

            @staticmethod
            def resolve_registry_context(**_kwargs):
                return {
                    "marketplaceId": "cell-a",
                    "installReceipt": str(cell / "install.json"),
                    "pluginRoot": str(cell),
                }

        monkeypatch.setattr(
            hook_client,
            "_load_sibling",
            lambda name: RegistryRoot if name == "registry_root.py" else None,
        )

        assert hook_client._request("preToolUse", {}, tmp_path) is None
    finally:
        server.close()


def test_explicit_context_session_start_selects_cell_runtime(
    monkeypatch, tmp_path
):
    cell = tmp_path / "cell"
    version = "1.2.3-dev4"
    slot = cell / "versions" / version
    executable = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    (slot / ".install-complete.json").write_text(
        json.dumps({"version": version}),
        encoding="utf-8",
    )
    (cell / "current-version").write_text(version, encoding="utf-8")
    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_CONTEXT",
        str(cell / "install.json"),
    )

    class RegistryRoot:
        @staticmethod
        def resolve_registry_root(**_kwargs):
            return cell

    monkeypatch.setattr(
        hook_client,
        "_load_sibling",
        lambda name: RegistryRoot if name == "registry_root.py" else None,
    )

    assert hook_client._runtime_python(tmp_path) == executable


def test_explicit_context_registration_context_uses_cell_session_context(
    monkeypatch, tmp_path
):
    cell = tmp_path / "cell"
    cell.mkdir(parents=True)
    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_CONTEXT",
        str(cell / "install.json"),
    )

    class RegistryRoot:
        @staticmethod
        def resolve_registry_root(**_kwargs):
            return cell

    monkeypatch.setattr(
        hook_client,
        "_load_sibling",
        lambda name: RegistryRoot if name == "registry_root.py" else None,
    )
    payload = {
        "sessionId": "session-1",
        "cwd": str(tmp_path),
        "source": "resume",
        "timestamp": 1_800_000_000.25,
        "_agentWorktrees": {"pluginVersion": "1.5.3-dev759"},
    }
    launch_key = hook_client._session_launch_key(payload)
    snapshots = cell / ".session-context"
    snapshots.mkdir(parents=True)
    (snapshots / f"register-session-{launch_key}.json").write_text(
        json.dumps(
            {
                "launchKey": launch_key,
                "output": '{"additionalContext":"cell-guidance"}',
            }
        ),
        encoding="utf-8",
    )

    assert hook_client._registration_context(payload, tmp_path) == "cell-guidance"


def test_explicit_context_session_start_never_uses_legacy_fallback(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_CONTEXT",
        str(tmp_path / "cell" / "install.json"),
    )
    python = tmp_path / "cell" / "versions" / "1.0.0" / "bin" / "python"
    monkeypatch.setattr(hook_client, "_runtime_python", lambda _home: python)
    monkeypatch.setattr(
        hook_client,
        "_fallback_legacy_session_start",
        lambda _payload: pytest.fail("legacy lifecycle must not run"),
    )
    payload = {
        "_agentWorktrees": {"pluginVersion": "1.0.1"},
    }

    assert hook_client._fallback_session_start(payload, tmp_path) == {}


def test_invalid_explicit_context_denies_before_guard_fallback(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_CONTEXT",
        str(tmp_path / "invalid" / "install.json"),
    )

    class InvalidRegistryRoot:
        @staticmethod
        def resolve_registry_root(**_kwargs):
            raise ValueError("invalid")

    monkeypatch.setattr(
        hook_client,
        "_load_sibling",
        lambda name: (
            InvalidRegistryRoot
            if name == "registry_root.py"
            else pytest.fail("guard ran before context validation")
        ),
    )

    result = hook_client._fallback_pre({}, tmp_path)

    assert result["permissionDecision"] == "deny"
    assert "invalid" in result["permissionDecisionReason"]


def test_explicit_context_session_end_uses_payload_client():
    hooks = json.loads(
        (Path(__file__).resolve().parents[1] / "hooks.json").read_text("utf-8")
    )
    entry = hooks["hooks"]["sessionEnd"][0]
    assert "COPILOT_PLUGIN_ROOT" in entry["powershell"]
    assert "hook_client.py" in entry["powershell"]
    assert "WindowsApps" in entry["powershell"]
    assert "COPILOT_PLUGIN_ROOT" in entry["bash"]
    assert 'python3 "$s" sessionEnd' in entry["bash"]


def test_hooks_json_does_not_register_user_prompt_submitted():
    """No ``userPromptSubmitted`` hook: the Phase 8 reopen mechanism this
    would have driven turned out to guard nothing -- ``prune.py``'s
    ``held_claims`` check already treats an ``at-rest`` (not just
    ``active``) session claim as live, and a claim only ever becomes
    ``released`` via a genuine ``sessionEnd``. A per-message hook here would
    only ever have flipped a claim's own display state, never the actual
    cleanup-eligibility decision -- not worth a subprocess-spawn cost on
    every submitted prompt."""
    hooks = json.loads(
        (Path(__file__).resolve().parents[1] / "hooks.json").read_text("utf-8")
    )
    assert "userPromptSubmitted" not in hooks["hooks"]


def test_explicit_context_session_end_uses_cell_runtime(
    monkeypatch, tmp_path
):
    python = tmp_path / "cell" / "versions" / "1.0.0" / "bin" / "python"
    seen = {}
    monkeypatch.setattr(hook_client, "_runtime_python", lambda _home: python)

    def run(argv, **kwargs):
        seen.update(argv=argv, kwargs=kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hook_client.subprocess, "run", run)
    payload = {"sessionId": "session-1", "cwd": str(tmp_path)}

    assert hook_client._fallback_session_end(payload, tmp_path) == {}
    assert seen["argv"] == [
        str(python),
        "-m",
        "agent_worktrees",
        "deregister-session",
        "--stdin",
    ]
    assert json.loads(seen["kwargs"]["input"]) == payload
    assert seen["kwargs"]["timeout"] == hook_client._SESSION_END_TIMEOUT_S


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
    from agent_worktrees import local_cache_refresh as _lcr_stub

    monkeypatch.setattr(
        _lcr_stub, "sessionstart_diagnostic", lambda cwd, **k: None
    )
    monkeypatch.setattr(
        main,
        "_migrate_legacy_marketplace_overrides",
        lambda payload, cwd: snapshots.append(
            ("marketplace-overrides-migration", "{}")
        ),
    )
    monkeypatch.setattr(
        main,
        "_reconcile_knowledge_plugin_overlay",
        lambda payload, cwd: snapshots.append(
            ("knowledge-plugin-overlay", "{}")
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
        ("marketplace-overrides-migration", "{}"),
        ("knowledge-plugin-overlay", "{}"),
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


def test_session_lifecycle_calls_local_cache_refresh_as_a_backup(
    monkeypatch, tmp_path
):
    """``_run_session_lifecycle`` repeats the worktree-scoped-dynamic-
    guidance render as a backup (docs/patterns/worktree-scoped-dynamic-
    guidance.md §4), independent of every other collaborator in the
    combined-lifecycle test above."""
    payload = {
        "sessionId": "session-1",
        "workingDirectory": str(tmp_path),
        "source": "new",
        "timestamp": 1_000,
    }
    monkeypatch.setattr(main.cfg, "active_project", lambda: None)
    monkeypatch.setattr(main.cfg, "set_active_project", lambda value: None)
    monkeypatch.setattr(main, "_start_project_session_hook", lambda cwd, env: None)
    monkeypatch.setattr(
        main, "_finish_project_session_hook", lambda process, deadline: ({}, "")
    )
    monkeypatch.setattr(main, "_registration_nudge_context", lambda cwd: "")
    monkeypatch.setattr(
        main, "_write_session_lifecycle_snapshot", lambda *a, **k: None
    )
    monkeypatch.setattr(
        main, "_write_session_lifecycle_receipt", lambda payload, state: None
    )
    monkeypatch.setattr(main, "cmd_register_session", lambda args: 0)
    monkeypatch.setattr(main, "_anchor_hygiene_diagnostic", lambda cwd: "")
    monkeypatch.setattr(
        main, "_migrate_legacy_marketplace_overrides", lambda payload, cwd: None
    )
    monkeypatch.setattr(
        main, "_reconcile_knowledge_plugin_overlay", lambda payload, cwd: None
    )
    monkeypatch.setattr(
        main, "_start_provisioning_if_needed", lambda cwd, environment: ""
    )

    calls = []
    from agent_worktrees import local_cache_refresh

    monkeypatch.setattr(
        local_cache_refresh,
        "refresh_local_cache",
        lambda repo_root, **k: calls.append(repo_root),
    )

    main._run_session_lifecycle(payload, deadline=time.time() + 200.0)

    assert calls == [str(tmp_path)]


def test_session_lifecycle_absorbs_local_cache_refresh_failure(
    monkeypatch, tmp_path
):
    """A failure inside the refresh (customizing-copilot not installed, a
    render error, anything) never surfaces as a session-lifecycle failure
    -- ``local_cache_refresh.sessionstart_diagnostic`` absorbs it silently,
    matching ``local_cache_refresh.refresh_local_cache``'s own best-effort
    contract.
    """
    payload = {
        "sessionId": "session-1",
        "workingDirectory": str(tmp_path),
        "source": "new",
        "timestamp": 1_000,
    }
    monkeypatch.setattr(main.cfg, "active_project", lambda: None)
    monkeypatch.setattr(main.cfg, "set_active_project", lambda value: None)
    monkeypatch.setattr(main, "_start_project_session_hook", lambda cwd, env: None)
    monkeypatch.setattr(
        main, "_finish_project_session_hook", lambda process, deadline: ({}, "")
    )
    monkeypatch.setattr(main, "_registration_nudge_context", lambda cwd: "")
    monkeypatch.setattr(
        main, "_write_session_lifecycle_snapshot", lambda *a, **k: None
    )
    monkeypatch.setattr(
        main, "_write_session_lifecycle_receipt", lambda payload, state: None
    )
    monkeypatch.setattr(main, "cmd_register_session", lambda args: 0)
    monkeypatch.setattr(main, "_anchor_hygiene_diagnostic", lambda cwd: "")
    monkeypatch.setattr(
        main, "_migrate_legacy_marketplace_overrides", lambda payload, cwd: None
    )
    monkeypatch.setattr(
        main, "_reconcile_knowledge_plugin_overlay", lambda payload, cwd: None
    )
    monkeypatch.setattr(
        main, "_start_provisioning_if_needed", lambda cwd, environment: ""
    )

    from agent_worktrees import local_cache_refresh

    def _boom(repo_root, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(local_cache_refresh, "refresh_local_cache", _boom)

    result = main._run_session_lifecycle(payload, deadline=time.time() + 200.0)

    assert "boom" not in str(result)


def test_migrate_legacy_marketplace_overrides_retires_marker(tmp_path):
    repo = tmp_path / "repo"
    settings_local = repo / ".github" / "copilot" / "settings.local.json"
    settings_local.parent.mkdir(parents=True)
    unrelated_value = {"source": {"source": "directory", "path": "/kept/as-is"}}
    settings_local.write_text(
        json.dumps(
            {
                "extraKnownMarketplaces": {
                    "example-knowledge-repo": {
                        "source": {
                            "source": "directory",
                            "path": "C:\\stale\\anchor\\.ai",
                        }
                    },
                    "operator-own": unrelated_value,
                },
                "_agentWorktreesMarketplaceOverrides": {
                    "version": 1,
                    "marketplaces": {
                        "example-knowledge-repo": {
                            "source": {
                                "source": "directory",
                                "path": "C:\\stale\\anchor\\.ai",
                            }
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    main._migrate_legacy_marketplace_overrides({}, str(repo))

    result = json.loads(settings_local.read_text(encoding="utf-8"))
    assert "_agentWorktreesMarketplaceOverrides" not in result
    assert "example-knowledge-repo" not in result.get("extraKnownMarketplaces", {})
    assert result["extraKnownMarketplaces"]["operator-own"] == unrelated_value


def test_migrate_legacy_marketplace_overrides_preserves_operator_edit(tmp_path):
    repo = tmp_path / "repo"
    settings_local = repo / ".github" / "copilot" / "settings.local.json"
    settings_local.parent.mkdir(parents=True)
    edited_value = {"source": {"source": "directory", "path": "/operator/edited"}}
    settings_local.write_text(
        json.dumps(
            {
                "extraKnownMarketplaces": {"example-knowledge-repo": edited_value},
                "_agentWorktreesMarketplaceOverrides": {
                    "version": 1,
                    "marketplaces": {
                        "example-knowledge-repo": {
                            "source": {
                                "source": "directory",
                                "path": "C:\\stale\\anchor\\.ai",
                            }
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    main._migrate_legacy_marketplace_overrides({}, str(repo))

    result = json.loads(settings_local.read_text(encoding="utf-8"))
    assert "_agentWorktreesMarketplaceOverrides" not in result
    # The operator changed this value after the marker was written, so the
    # migration must not clobber it -- only exactly-unmodified marker-owned
    # values are retired.
    assert result["extraKnownMarketplaces"]["example-knowledge-repo"] == edited_value


def test_migrate_legacy_marketplace_overrides_no_marker_is_noop(tmp_path):
    repo = tmp_path / "repo"
    settings_local = repo / ".github" / "copilot" / "settings.local.json"
    settings_local.parent.mkdir(parents=True)
    original = {"extraKnownMarketplaces": {"other": {"source": {"source": "github"}}}}
    settings_local.write_text(json.dumps(original), encoding="utf-8")

    main._migrate_legacy_marketplace_overrides({}, str(repo))

    assert json.loads(settings_local.read_text(encoding="utf-8")) == original


def test_migrate_legacy_marketplace_overrides_no_file_is_noop(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # Must not raise or create a file when there's nothing to migrate.
    main._migrate_legacy_marketplace_overrides({}, str(repo))
    assert not (repo / ".github" / "copilot" / "settings.local.json").exists()


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
