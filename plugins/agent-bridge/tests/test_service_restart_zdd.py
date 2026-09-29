"""``service restart`` must be the same ZDD cutover as ``deploy`` (#1362 /
efforts/active/agent-bridge-unified-zdd-cutover Phase 1).

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
    # Uses the real `build_parser()` (not the standalone `_build_parser()`
    # helper above) because `.json` is only guaranteed present once the
    # top-level `--json` flag has run -- see
    # test_service_restart_json_does_not_shadow_global_json below.
    parser = core.build_parser()
    args = parser.parse_args(["service", "restart"])
    for attr in ("health_timeout", "drain_timeout", "force", "json"):
        assert hasattr(args, attr), f"service restart args missing .{attr}"


def test_service_restart_json_does_not_shadow_global_json():
    # Regression guard, same class as
    # test_session_selection.py's
    # test_global_json_flag_survives_into_resume_namespace: a subparser that
    # redeclares `--json` with `default=False` silently overwrites the
    # top-level `--json` flag's already-True value once the subparser
    # applies its own default, because both share the Namespace's `json`
    # dest. `add_deploy_cutover_flags()` must use
    # `default=argparse.SUPPRESS` so `agent-bridge --json service restart`
    # (the canonical global-flag-before-command form) keeps `args.json is
    # True`, and `agent-bridge deploy --json` (flag given at the subcommand
    # itself) still works too.
    parser = core.build_parser()

    args = parser.parse_args(["--json", "service", "restart"])
    assert args.json is True

    args = parser.parse_args(["service", "restart", "--json"])
    assert args.json is True

    args = parser.parse_args(["service", "restart"])
    assert args.json is False

    # `deploy` itself must show the same fix, not just `restart`.
    args = parser.parse_args(["--json", "deploy"])
    assert args.json is True


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


def test_deploy_json_output_is_not_corrupted_by_recovery_messages(
    tmp_path, monkeypatch, capsys
):
    # Regression guard: `_cmd_deploy --json` (now also reachable via
    # `service restart --json`, since Phase 1 routes restart through it)
    # used to print "[>] Recovered a prior aborted cutover: ..." /
    # "[>] Reaped an abandoned never-promoted passive ..." to stdout
    # *unconditionally*, ahead of the single `core._json_out(res.to_dict())`
    # call -- corrupting the JSON payload whenever a restart/deploy happened
    # to heal a stale cutover or reap an abandoned passive. Both messages
    # must now surface as `steps` entries inside the one JSON object
    # instead of separate prints.
    from agent_bridge import config as agent_bridge_config
    from zdd import breadcrumb as zdd_breadcrumb
    from zdd import cutover as zdd_cutover
    from zdd import routing as zdd_routing

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setattr(agent_bridge_config, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr(
        agent_bridge_config,
        "load_config",
        lambda: argparse.Namespace(bind="127.0.0.1"),
    )
    monkeypatch.setattr(
        agent_bridge_config, "load_or_create_auth_token", lambda: "tok"
    )
    monkeypatch.setattr(zdd_breadcrumb, "read_breadcrumb", lambda _cfg: None)
    monkeypatch.setattr(
        zdd_breadcrumb,
        "recover_stale_cutover",
        lambda *a, **k: {"recovered": True, "reason": "undrained a stale survivor"},
    )
    monkeypatch.setattr(
        core,
        "_reap_abandoned_passive",
        lambda *a, **k: {"reaped": True, "pid": 4242},
    )
    monkeypatch.setattr(
        zdd_routing, "read_active_endpoint", lambda *a, **k: None
    )

    fake_result = zdd_cutover.CutoverResult(ok=True, new_port=4321, steps=["spawned passive"])

    class FakeOrchestrator:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, **kwargs):
            return fake_result

    monkeypatch.setattr(zdd_cutover, "CutoverOrchestrator", FakeOrchestrator)

    parser = core.build_parser()
    args = parser.parse_args(["--json", "deploy"])

    with pytest.raises(SystemExit) as excinfo:
        venue_cli._cmd_deploy(args)
    assert excinfo.value.code == 0

    out = capsys.readouterr().out
    import json

    # Pretty-printed JSON spans multiple lines; the regression this guards
    # against is a *stray line before/after* the JSON object, not multi-line
    # JSON itself -- json.loads(out) fails outright if anything else got
    # printed to stdout ahead of (or after) the payload.
    payload = json.loads(out)
    assert payload["ok"] is True
    assert "recovered a prior aborted cutover" in " ".join(payload["steps"])
    assert "reaped an abandoned never-promoted passive" in " ".join(payload["steps"])
