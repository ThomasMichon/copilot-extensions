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


def test_cutover_serializes_against_concurrent_route_lock(tmp_path, monkeypatch):
    """``deploy``/``_cutover`` must share the same routing-transition lock
    ``serve()``'s non-passive guard uses -- otherwise a cold-start deploy and
    a direct non-passive ``serve()`` can each observe no live coordinator and
    race the flip, recreating the undrained-duplicate incident this guard
    exists to prevent (review follow-up on
    ThomasMichon/copilot-extensions#3066)."""
    import zdd.breadcrumb as zdd_breadcrumb
    import zdd.cutover as zdd_cutover

    from agent_dispatch import __main__ as main_mod
    from agent_dispatch.single_instance import SingleInstance

    routing = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))
    monkeypatch.setattr("agent_dispatch.config.client_token", lambda: None)
    monkeypatch.setattr(zdd_breadcrumb, "recover_stale_cutover", lambda *a, **k: {"recovered": False})
    monkeypatch.setattr(zdd_breadcrumb, "read_breadcrumb", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_reap_abandoned_passive", lambda *_a, **_k: {"reaped": False})
    monkeypatch.setattr(main_mod, "_reap_superseded_coordinators", lambda *_a, **_k: None)

    seen: dict = {}

    class _FakeResult:
        ok = True
        new_port = 1234
        error = None
        rolled_back = False
        steps: list = []

        def to_dict(self):
            return {"ok": True}

    class _FakeOrchestrator:
        def __init__(self, *_a, **_k):
            pass

        def run(self, **_kwargs):
            # While "running", the lock must be held -- a concurrent acquire
            # attempt (as a direct `serve()` would make) must fail.
            contender = SingleInstance(routing / "serve-start.lock")
            seen["acquired_during_run"] = contender.acquire()
            if seen["acquired_during_run"]:
                contender.release()
            return _FakeResult()

    monkeypatch.setattr(zdd_cutover, "CutoverOrchestrator", _FakeOrchestrator)

    parser = main_mod.build_parser()
    ns = parser.parse_args(["deploy", "--json"])
    exit_code = main_mod._cmd_cutover(ns)

    assert exit_code == 0
    assert seen["acquired_during_run"] is False, "lock must be held during orch.run()"
    # ... and released afterward, for a subsequent attempt.
    probe = SingleInstance(routing / "serve-start.lock")
    assert probe.acquire(), "lock was not released after the cutover completed"
    probe.release()


def test_cutover_holds_lock_across_recovery_preamble(tmp_path, monkeypatch):
    """The lock must cover ``recover_stale_cutover``/``_reap_abandoned_passive``
    too, not just ``orch.run()`` -- those mutate breadcrumb/daemon state and
    treat any non-terminal breadcrumb as stale, so a second `deploy` could
    otherwise undrain or reap an in-progress cutover while the first
    orchestrator is still running (review follow-up on
    ThomasMichon/copilot-extensions#3066)."""
    import zdd.breadcrumb as zdd_breadcrumb
    import zdd.cutover as zdd_cutover

    from agent_dispatch import __main__ as main_mod
    from agent_dispatch.single_instance import SingleInstance

    routing = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))
    monkeypatch.setattr("agent_dispatch.config.client_token", lambda: None)

    seen: dict = {}

    def _fake_recover(*_a, **_k):
        contender = SingleInstance(routing / "serve-start.lock")
        seen["acquired_during_recover"] = contender.acquire()
        if seen["acquired_during_recover"]:
            contender.release()
        return {"recovered": False}

    monkeypatch.setattr(zdd_breadcrumb, "recover_stale_cutover", _fake_recover)
    monkeypatch.setattr(zdd_breadcrumb, "read_breadcrumb", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_reap_abandoned_passive", lambda *_a, **_k: {"reaped": False})
    monkeypatch.setattr(main_mod, "_reap_superseded_coordinators", lambda *_a, **_k: None)

    class _FakeResult:
        ok = True
        new_port = 1234
        error = None
        rolled_back = False
        steps: list = []

        def to_dict(self):
            return {"ok": True}

    class _FakeOrchestrator:
        def __init__(self, *_a, **_k):
            pass

        def run(self, **_kwargs):
            return _FakeResult()

    monkeypatch.setattr(zdd_cutover, "CutoverOrchestrator", _FakeOrchestrator)

    parser = main_mod.build_parser()
    ns = parser.parse_args(["deploy", "--json"])
    exit_code = main_mod._cmd_cutover(ns)

    assert exit_code == 0
    assert seen["acquired_during_recover"] is False, (
        "lock must already be held during the recovery preamble"
    )


def test_cutover_holds_lock_through_post_cutover_reap(tmp_path, monkeypatch):
    """The lock must also cover ``_reap_superseded_coordinators(result)`` --
    otherwise a second `deploy` could acquire it in the gap right after
    release, spawn its own new passive coordinator, and have this command's
    reaper (which only knows about its own before/after PIDs) terminate that
    still-passive process as an apparent leftover (review follow-up on
    ThomasMichon/copilot-extensions#3066)."""
    import zdd.breadcrumb as zdd_breadcrumb
    import zdd.cutover as zdd_cutover

    from agent_dispatch import __main__ as main_mod
    from agent_dispatch.single_instance import SingleInstance

    routing = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing))
    monkeypatch.setattr("agent_dispatch.config.client_token", lambda: None)
    monkeypatch.setattr(zdd_breadcrumb, "recover_stale_cutover", lambda *a, **k: {"recovered": False})
    monkeypatch.setattr(zdd_breadcrumb, "read_breadcrumb", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "_reap_abandoned_passive", lambda *_a, **_k: {"reaped": False})

    seen: dict = {}

    def _fake_reap_superseded(_result):
        contender = SingleInstance(routing / "serve-start.lock")
        seen["acquired_during_reap"] = contender.acquire()
        if seen["acquired_during_reap"]:
            contender.release()

    monkeypatch.setattr(main_mod, "_reap_superseded_coordinators", _fake_reap_superseded)

    class _FakeResult:
        ok = True
        new_port = 1234
        error = None
        rolled_back = False
        steps: list = []

        def to_dict(self):
            return {"ok": True}

    class _FakeOrchestrator:
        def __init__(self, *_a, **_k):
            pass

        def run(self, **_kwargs):
            return _FakeResult()

    monkeypatch.setattr(zdd_cutover, "CutoverOrchestrator", _FakeOrchestrator)

    parser = main_mod.build_parser()
    ns = parser.parse_args(["deploy", "--json"])
    exit_code = main_mod._cmd_cutover(ns)

    assert exit_code == 0
    assert seen["acquired_during_reap"] is False, (
        "lock must still be held during the post-cutover reap"
    )
    probe = SingleInstance(routing / "serve-start.lock")
    assert probe.acquire(), "lock was not released after the reap completed"
    probe.release()


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
