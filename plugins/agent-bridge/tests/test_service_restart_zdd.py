"""``service restart`` must be the same ZDD cutover as ``deploy`` (dotfiles
#1362 / efforts/active/agent-bridge-unified-zdd-cutover Phase 1).

Before this, ``service restart`` was a raw ``_service_stop()`` +
``_service_start()`` -- no cutover orchestrator, no health gate, no drain,
and no session-host awareness, unlike ``deploy``'s real
``zdd.cutover.CutoverOrchestrator`` flow. This test locks in that
``restart`` now routes through ``_cmd_deploy`` and never calls the raw
stop/start pair directly.
"""

from __future__ import annotations

import argparse

import pytest

from agent_bridge import __main__ as core
from agent_bridge import service_process_cli as spc
from agent_bridge import venue_cli

# Fast parser/routing contract checks (no I/O, no daemon) -- covered by the
# `--guards` fast contract lane per TESTING.md.
pytestmark = pytest.mark.guard


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    spc.register_service_control_commands(sub)
    return parser


def test_service_restart_parses_deploy_style_flags():
    parser = _build_parser()
    args = parser.parse_args(
        ["service", "restart", "--force", "--health-timeout", "30", "--json"]
    )
    assert args.force is True
    assert args.health_timeout == 30.0
    assert args.json is True


def test_service_restart_calls_cmd_deploy_not_raw_stop_start(monkeypatch):
    calls: list[str] = []
    # `_cmd_service` resolves the daemon lifecycle helpers via
    # `core = _core()` (`agent_bridge.__main__`), not the module-level
    # names in `service_process_cli` itself -- patch the same attributes
    # `_cmd_service` would actually dereference for start/stop, so a
    # regression back to a raw stop-then-start fails this test.
    monkeypatch.setattr(core, "_service_stop", lambda: calls.append("stop"))
    monkeypatch.setattr(core, "_service_start", lambda: calls.append("start"))

    def fake_cmd_deploy(args):
        calls.append("deploy")
        # _cmd_deploy always exits explicitly -- match that contract.
        raise SystemExit(0)

    monkeypatch.setattr(venue_cli, "_cmd_deploy", fake_cmd_deploy)

    parser = _build_parser()
    args = parser.parse_args(["service", "restart"])

    with pytest.raises(SystemExit) as excinfo:
        spc._cmd_service(args)

    assert excinfo.value.code == 0
    assert calls == ["deploy"]


def test_service_restart_args_have_every_attribute_cmd_deploy_reads():
    # `_cmd_deploy` accesses `.json` directly (not via getattr) in several
    # branches; a restart-built Namespace missing it would AttributeError
    # deep inside a real cutover instead of failing this cheap parse check.
    parser = _build_parser()
    args = parser.parse_args(["service", "restart"])
    for attr in ("health_timeout", "drain_timeout", "force", "json"):
        assert hasattr(args, attr), f"service restart args missing .{attr}"


def test_service_restart_does_not_expose_recover():
    # `--recover` is a deploy-only maintenance mode: `_cmd_deploy` exits
    # immediately after healing a prior aborted cutover, *without* starting
    # a new one. Exposing it on `restart` would let
    # `agent-bridge service restart --recover` return success while never
    # actually restarting the daemon. `_cmd_deploy` reads it via
    # `getattr(args, "recover", False)`, so simply not registering the flag
    # keeps it always-False for restart.
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["service", "restart", "--recover"])

    args = parser.parse_args(["service", "restart"])
    assert getattr(args, "recover", False) is False


def test_deploy_still_exposes_recover():
    # The shared flag helper must not have dropped `--recover` from `deploy`
    # itself while excluding it from `restart`.
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    venue_cli.register_venue_commands(sub)
    args = parser.parse_args(["deploy", "--recover"])
    assert args.recover is True
