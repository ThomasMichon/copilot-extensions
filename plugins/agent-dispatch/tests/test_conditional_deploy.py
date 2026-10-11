"""Conditional deploy skips caller env, start-lock and abandoned-passive preludes."""

from __future__ import annotations

import argparse
import json
import os

import pytest

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


@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.parametrize("phase", ["recovery", "reap"])
def test_ordinary_dispatch_deploy_refuses_conditional_recovery(
    tmp_path, monkeypatch, capsys, recover, phase
):
    from agent_dispatch.config import Config

    monkeypatch.setattr("agent_dispatch.install_paths.apply_service_env_overlay", lambda *_: None)
    monkeypatch.setattr(coordinator_cli._config, "routing_dir", lambda: tmp_path)
    monkeypatch.setattr(coordinator_cli._config, "load_config", lambda: Config(db_path="fixture.db"))
    monkeypatch.setattr(coordinator_cli, "_client_token_value", lambda: None)
    monkeypatch.setattr(coordinator_cli, "_emit", lambda data: print(json.dumps(data)))

    class Lock:
        def __init__(self, *_args):
            pass

        def acquire(self):
            return True

        def release(self):
            pass

    def forbidden(*args, **kwargs):
        pytest.fail("ordinary deploy must not probe/reap conditional generations")

    monkeypatch.setattr("agent_dispatch.single_instance.SingleInstance", Lock)
    monkeypatch.setattr(coordinator_cli._urllib, "urlopen", forbidden)
    monkeypatch.setattr(coordinator_cli, "_core_helper", lambda name, local: forbidden)
    breadcrumb.write_breadcrumb(tmp_path, state="conditional-draining", new_pid=123)
    if phase == "reap":
        monkeypatch.setattr(breadcrumb, "recover_stale_cutover", lambda *_a, **_k: {})
        monkeypatch.setattr(
            coordinator_cli, "_core_helper",
            lambda *_a: lambda *_b: {
                "refused": True, "reason": "conditional cutover requires owner-led recovery"
            },
        )
    before = breadcrumb.breadcrumb_path(tmp_path).read_bytes()
    args = argparse.Namespace(
        json=True, force=False, recover=recover, health_timeout=0, drain_timeout=0
    )
    assert coordinator_cli._cmd_cutover(args) == 1
    assert "owner-led recovery" in json.loads(capsys.readouterr().out)["error"]
    assert breadcrumb.breadcrumb_path(tmp_path).read_bytes() == before
