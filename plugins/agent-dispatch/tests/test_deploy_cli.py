"""Tests for the public ``deploy`` CLI verb.

``deploy`` is the operator-facing name for the zero-downtime graceful cutover
(mirroring ``agent-bridge deploy``); ``_cutover`` remains a hidden back-compat
alias for any existing installer script. Both must route to the exact same
handler so there is only one cutover code path to trust.
"""

from __future__ import annotations

import argparse

from agent_dispatch.__main__ import _cmd_cutover, build_parser


def test_deploy_and_cutover_route_to_the_same_handler():
    parser = build_parser()
    deploy_ns = parser.parse_args(["deploy"])
    cutover_ns = parser.parse_args(["_cutover"])
    assert deploy_ns.func is _cmd_cutover
    assert cutover_ns.func is _cmd_cutover


def test_deploy_accepts_the_same_flags_as_cutover():
    parser = build_parser()
    ns = parser.parse_args([
        "deploy",
        "--health-timeout", "10",
        "--drain-timeout", "20",
        "--force",
        "--recover",
        "--json",
    ])
    assert ns.health_timeout == 10.0
    assert ns.drain_timeout == 20.0
    assert ns.force is True
    assert ns.recover is True
    assert ns.json is True


def test_deploy_is_publicly_documented_not_suppressed():
    parser = build_parser()
    sub = next(
        a for a in parser._subparsers._group_actions  # noqa: SLF001
        if a.dest == "command"
    )
    deploy_choice = sub.choices["deploy"]
    assert deploy_choice.format_help() != ""
    help_texts = {a.dest: a.help for a in sub._choices_actions}  # noqa: SLF001
    assert "zero-downtime" in (help_texts.get("deploy") or "").lower()
    assert help_texts.get("_cutover") == argparse.SUPPRESS


def test_reap_abandoned_passive_delegates_to_reap_module(tmp_path, monkeypatch):
    """#5195: the CLI-level wrapper is a thin, correctly-anchored delegate to
    ``agent_dispatch.reap.reap_abandoned_passive_backstop``."""
    from agent_dispatch import __main__ as main_mod

    monkeypatch.setattr("agent_dispatch.config.routing_dir", lambda: tmp_path)

    seen: dict = {}

    def fake_backstop(config_dir, *, record, grace_seconds=None):
        seen["config_dir"] = config_dir
        seen["record"] = record
        seen["grace_seconds"] = grace_seconds
        return {"reaped": True, "reason": "terminated", "pid": 4321}

    monkeypatch.setattr(
        "agent_dispatch.reap.reap_abandoned_passive_backstop", fake_backstop,
    )
    result = main_mod._reap_abandoned_passive(
        {"state": "started", "new_pid": 4321}, grace_seconds=42.0,
    )
    assert result == {"reaped": True, "reason": "terminated", "pid": 4321}
    assert seen["config_dir"] == tmp_path
    assert seen["record"] == {"state": "started", "new_pid": 4321}
    assert seen["grace_seconds"] == 42.0
