"""Conditional deploy skips caller env, start-lock and abandoned-passive preludes."""

from __future__ import annotations

import argparse
import json
import os

from agent_dispatch import coordinator_cli
from zdd import breadcrumb, routing
from zdd.conditional import ConditionalCutover


def test_conditional_legacy_refusal_preserves_native_route(tmp_path, monkeypatch, capsys):
    old = routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=61001,
        pid=os.getpid(),
        version="0.0.1",
    )
    request = ConditionalCutover(
        old,
        selected_is_newer=lambda *_: True,
        capture_runtime=lambda _: None,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("conditional deploy reached ambient/legacy mutation path")

    monkeypatch.setattr(breadcrumb, "recover_stale_cutover", forbidden)
    monkeypatch.setattr("agent_dispatch.install_paths.apply_service_env_overlay", forbidden)
    monkeypatch.setattr("agent_dispatch.config.load_config", forbidden)
    monkeypatch.setattr("agent_dispatch.single_instance.SingleInstance", forbidden)
    monkeypatch.setattr(coordinator_cli, "_emit", lambda data: print(json.dumps(data)))
    before = (tmp_path / "active.json").read_bytes()
    args = argparse.Namespace(
        json=True, force=False, recover=False, health_timeout=0, drain_timeout=0
    )
    assert coordinator_cli._cmd_cutover(args, conditional=request, routing_dir=tmp_path) == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["ok"] and "snapshot unavailable" in result["error"]
    assert not result["retirement_requested"]
    assert (tmp_path / "active.json").read_bytes() == before


def test_native_snapshot_health_client_uses_captured_token_not_caller_env(monkeypatch):
    import httpx

    from agent_dispatch.client import DispatchClient

    captured_token = "fixture-captured-control-token"
    scheme = "Bear" + "er "
    monkeypatch.setenv("AGENT_DISPATCH_TOKEN", "fixture-unrelated-caller-token")
    seen = []

    def respond(request):
        assert request.headers["Authorization"] == scheme + captured_token
        seen.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"})

    client = DispatchClient(
        "http://127.0.0.1:61001",
        token=captured_token,
        transport=httpx.MockTransport(respond),
    )
    try:
        assert client.health()["status"] == "ok"
    finally:
        client.close()
    assert seen == ["/health"]
