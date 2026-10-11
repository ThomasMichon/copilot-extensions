"""Conditional restart enters native authority, not legacy recovery or force-reap."""

from __future__ import annotations

import argparse
import json
import os

import pytest

from agent_bridge import service_process_cli, venue_cli
from zdd import breadcrumb, routing
from zdd.conditional import ConditionalCutover


@pytest.mark.parametrize("service", [False, True])
def test_conditional_legacy_refusal_never_uses_ambient_context(
    tmp_path,
    monkeypatch,
    capsys,
    service,
):
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
        pytest.fail("legacy recovery, ambient configuration or force-retirement was reached")

    monkeypatch.setattr(breadcrumb, "recover_stale_cutover", forbidden)
    monkeypatch.setattr("agent_bridge.config.load_config", forbidden)
    monkeypatch.setattr("agent_bridge.config.load_or_create_auth_token", forbidden)
    monkeypatch.setattr(venue_cli._core(), "_ensure_retired_daemon_exited", forbidden)
    before = (tmp_path / "active.json").read_bytes()
    args = argparse.Namespace(
        json=True,
        force=False,
        recover=False,
        health_timeout=0,
        drain_timeout=0,
        service_action="restart",
    )
    command = service_process_cli._cmd_service if service else venue_cli._cmd_deploy
    with pytest.raises(SystemExit) as exc:
        command(args, conditional=request, routing_dir=tmp_path)
    assert exc.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and "snapshot unavailable" in result["error"]
    assert result["retirement_requested"] is False
    assert result["serving_endpoint"] is None
    assert (tmp_path / "active.json").read_bytes() == before


def test_conditional_bridge_forward_refuses_before_capture(tmp_path, capsys):
    endpoint = routing.Endpoint(
        "127.0.0.1",
        61001,
        pid=os.getpid(),
        version="0.0.1",
        generation=1,
        process_start_time="1",
    )
    routing._atomic_write(
        tmp_path / "active.json",
        {
            "active": {**endpoint.to_dict(), "forwarded": True},
        },
    )
    request = ConditionalCutover(
        endpoint,
        selected_is_newer=lambda *_: True,
        capture_runtime=lambda _: pytest.fail("forward must not capture a local context"),
    )
    args = argparse.Namespace(
        json=True, force=False, recover=False, health_timeout=0, drain_timeout=0
    )
    with pytest.raises(SystemExit) as exc:
        venue_cli._cmd_deploy(args, conditional=request, routing_dir=tmp_path)
    assert exc.value.code == 1
    assert "forward" in json.loads(capsys.readouterr().out)["error"]


def test_native_bridge_cutover_health_probe_remains_anonymous(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from zdd.cutover import CutoverResult

    captured = {}
    core = venue_cli._core()
    monkeypatch.setattr(core, "_active_endpoint_is_forward", lambda: False)
    monkeypatch.setattr(core, "_reap_abandoned_passive", lambda *_: {"reaped": False})
    monkeypatch.setattr("agent_bridge.config.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "agent_bridge.config.load_config", lambda: SimpleNamespace(bind="127.0.0.1")
    )
    monkeypatch.setattr(
        "agent_bridge.config.load_or_create_auth_token", lambda: "fixture-control-token"
    )
    monkeypatch.setattr(breadcrumb, "recover_stale_cutover", lambda *_a, **_k: {})

    class Orchestrator:
        def __init__(self, *_args, health_check, **kwargs):
            captured["probe"] = health_check

        def run(self, **kwargs):
            return CutoverResult(ok=False, error="fixture")

    monkeypatch.setattr("zdd.cutover.CutoverOrchestrator", Orchestrator)
    args = argparse.Namespace(
        json=True, force=False, recover=False, health_timeout=0, drain_timeout=0
    )
    with pytest.raises(SystemExit):
        venue_cli._cmd_deploy(args)

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"status":"ok","ready":true}'

    def respond(url, **kwargs):
        assert isinstance(url, str)  # no Request object carrying an Authorization header
        assert url == "http://127.0.0.1:61001/health"
        return Response()

    monkeypatch.setattr(venue_cli.urllib.request, "urlopen", respond)
    assert captured["probe"]("127.0.0.1", 61001)


@pytest.mark.parametrize("recover", [False, True])
@pytest.mark.parametrize("phase", ["recovery", "reap"])
def test_ordinary_deploy_refuses_unresolved_conditional_owner(
    tmp_path, monkeypatch, capsys, recover, phase
):
    from types import SimpleNamespace

    core = venue_cli._core()
    monkeypatch.setattr(core, "_active_endpoint_is_forward", lambda: False)
    monkeypatch.setattr("agent_bridge.config.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "agent_bridge.config.load_config", lambda: SimpleNamespace(bind="127.0.0.1")
    )
    monkeypatch.setattr("agent_bridge.config.load_or_create_auth_token", lambda: "fixture-token")

    def forbidden(*args, **kwargs):
        pytest.fail("ordinary deploy must not recover/reap/probe conditional generations")

    monkeypatch.setattr(core, "_reap_abandoned_passive", forbidden)
    monkeypatch.setattr(venue_cli.urllib.request, "urlopen", forbidden)
    breadcrumb.write_breadcrumb(tmp_path, state="conditional-draining", new_pid=123)
    if phase == "reap":
        monkeypatch.setattr(breadcrumb, "recover_stale_cutover", lambda *_a, **_k: {})
        monkeypatch.setattr(
            core, "_reap_abandoned_passive",
            lambda *_a: {"refused": True, "reason": "conditional cutover requires owner-led recovery"},
        )
    before = breadcrumb.breadcrumb_path(tmp_path).read_bytes()
    args = argparse.Namespace(
        json=True, force=False, recover=recover, health_timeout=0, drain_timeout=0
    )
    with pytest.raises(SystemExit) as exc:
        venue_cli._cmd_deploy(args)
    assert exc.value.code == 1
    assert "owner-led recovery" in json.loads(capsys.readouterr().out)["error"]
    assert breadcrumb.breadcrumb_path(tmp_path).read_bytes() == before
