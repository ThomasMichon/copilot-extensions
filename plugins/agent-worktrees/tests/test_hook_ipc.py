from __future__ import annotations

import importlib.util
import json
import socket
import sys
import time
from pathlib import Path

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
    assert "-m agent_worktrees" not in text
