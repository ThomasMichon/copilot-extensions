"""Tests for the Copilot-free session peek (peek_snapshot.py + target_exec.py)."""

from __future__ import annotations

import json
import os

import pytest

from agent_bridge import peek_snapshot as ps
from agent_bridge import target_exec as tx


def _write_session(root: str, acp_id: str, events: list[dict]) -> str:
    sdir = os.path.join(root, acp_id)
    os.makedirs(sdir)
    with open(os.path.join(sdir, "events.jsonl"), "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    return sdir


_ACP = "079691b3-9bb3-470e-ab1c-e4f628e11062"


def _events(*, resumed: bool = False, shutdown: str | None = None) -> list[dict]:
    evs = [
        {"type": "session.start", "data": {}, "timestamp": "2026-08-14T00:00:00Z"},
        {"type": "session.model_change", "data": {"modelId": "claude-opus-4.8"},
         "timestamp": "2026-08-14T00:00:01Z"},
        {"type": "system.message", "data": {"text": "You are the CLI. " * 50},
         "timestamp": "2026-08-14T00:00:02Z"},
        {"type": "user.message", "data": {"content": "Reply READY"},
         "timestamp": "2026-08-14T00:00:03Z"},
        {"type": "assistant.message", "data": {"content": [{"text": "READY"}]},
         "timestamp": "2026-08-14T00:00:04Z"},
        {"type": "session.usage_checkpoint",
         "data": {"totalPremiumRequests": 2, "totalNanoAiu": 11346730000},
         "timestamp": "2026-08-14T00:00:05Z"},
    ]
    if resumed:
        evs.append({"type": "session.resume", "data": {},
                    "timestamp": "2026-08-14T00:01:00Z"})
    if shutdown:
        evs.append({"type": "session.shutdown", "data": {"shutdownType": shutdown},
                    "timestamp": "2026-08-14T00:02:00Z"})
    return evs


def test_driver_compiles():
    compile(ps._DRIVER, "<driver>", "exec")


def test_snapshot_local_parses_transcript(tmp_path):
    root = str(tmp_path)
    _write_session(root, _ACP, _events(shutdown="routine"))
    snap = ps.snapshot_local(_ACP, session_state_root=root)
    assert snap["ok"] is True
    assert snap["acp_session_id"] == _ACP
    assert snap["turns"] == 1
    assert snap["model"] == "claude-opus-4.8"
    assert snap["usage"]["premium_requests"] == 2
    # system prompt is excluded from the recent tail (user+assistant only)
    roles = [m["role"] for m in snap["recent_messages"]]
    assert "system" not in roles
    assert roles == ["user", "assistant"]
    assert snap["lifecycle"]["clean_shutdown"] is True


def test_snapshot_local_missing_session(tmp_path):
    snap = ps.snapshot_local(_ACP, session_state_root=str(tmp_path))
    assert snap["ok"] is False


def test_reuse_verdict_risky_on_resume_without_clean_shutdown(tmp_path):
    root = str(tmp_path)
    _write_session(root, _ACP, _events(resumed=True))  # resume, no shutdown
    snap = ps.snapshot_local(_ACP, session_state_root=root)
    verdict, reason = ps.reuse_verdict(snap)
    assert verdict == "risky"
    assert "clean shutdown" in reason


def test_reuse_verdict_reusable(tmp_path):
    root = str(tmp_path)
    _write_session(root, _ACP, _events(shutdown="routine"))
    verdict, _ = ps.reuse_verdict(snap := ps.snapshot_local(_ACP, session_state_root=root))
    assert snap["ok"] and verdict == "reusable"


def test_reuse_verdict_none_when_no_transcript():
    verdict, _ = ps.reuse_verdict({"ok": False, "reason": "x"})
    assert verdict == "none"


def test_build_peek_command_rejects_injection():
    with pytest.raises(ValueError):
        ps.build_peek_command("bad; rm -rf /")
    cmd = ps.build_peek_command(_ACP)
    assert ps.RESULT_MARKER in cmd  # empty-result fallback marker embedded


def test_parse_peek_result_ignores_noise():
    good = json.dumps({"ok": True, "turns": 3})
    out = f"login banner\nsome hook noise\n{ps.RESULT_MARKER}{good}\ntrailing\n"
    snap = ps.parse_peek_result(out)
    assert snap["ok"] is True and snap["turns"] == 3
    assert ps.parse_peek_result("no marker here")["ok"] is False


def test_target_kind_and_name():
    cs = {"agent_name": "codespace:my-cs"}
    assert tx.target_kind(cs) == "codespace"
    assert tx.codespace_name(cs) == "my-cs"
    assert tx.target_kind({"agent_name": "local-agent", "target_type": "local"}) == "local"
    assert tx.target_kind({"target_type": "command"}) == "local"
    # A real container session persists target_type: "command" (the generic
    # provider-driven type) -- only its agent_name prefix distinguishes it.
    # Without checking that prefix, this fell through to "local" (#2042
    # follow-up), defeating any non-local-kind gate regardless of what it
    # checks.
    assert tx.target_kind({
        "agent_name": "container:my-container", "target_type": "command",
    }) == "container"


def test_exec_bash_on_target_unsupported_transport():
    with pytest.raises(tx.TargetExecError):
        tx.exec_bash_on_target({"target_type": "ssh"}, "echo hi", timeout=5)


def test_cmd_peek_container_target_fails_closed_instead_of_reading_local(
    monkeypatch,
):
    """A container (or any non-local, non-codespace) target must never fall
    back to reading THIS host's local events.jsonl -- that transcript lives on
    the remote target, not here. It must fail closed via TargetExecError
    instead of silently returning a wrong-filesystem snapshot.
    """
    import argparse

    from agent_bridge import __main__ as main_mod

    session = {
        "session_id": "s1",
        "agent_name": "container:odsp-web-1",
        # A real container session persists target_type: "command" (the
        # generic provider-driven type) -- the container/codespace distinction
        # lives only in the agent_name prefix. Using "command" here (not the
        # unrealistic "container") is what actually exercises target_kind()'s
        # prefix check end-to-end.
        "target_type": "command",
        "acp_session_id": _ACP,
    }

    class FakeClient:
        def get_session(self, _target):
            return session

        def list_sessions(self):
            return [session]

    monkeypatch.setattr(main_mod, "_get_client", lambda: FakeClient())

    def _forbidden_snapshot_local(*_args, **_kwargs):
        raise AssertionError(
            "snapshot_local must not be used for a non-local target"
        )

    monkeypatch.setattr(ps, "snapshot_local", _forbidden_snapshot_local)

    args = argparse.Namespace(
        target="s1", tail=400, recent=8, message_chars=400,
        timeout=90.0, stale_hours=6.0, json=False,
    )

    with pytest.raises(SystemExit) as exc_info:
        main_mod._cmd_peek(args)
    assert exc_info.value.code == 1


# -- presence ------------------------------------------------------------------------


def _ev(kind: str, at: int, **data) -> dict:
    return {"type": kind, "data": data, "id": f"e{at}", "timestamp": f"2026-10-06T00:00:{at:02}Z"}


def _presence(tmp_path, events: list[dict], *, raw_tail: str = "") -> dict:
    root = str(tmp_path)
    sdir = _write_session(root, _ACP, events)
    if raw_tail:
        with open(os.path.join(sdir, "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(raw_tail)
    snap = ps.snapshot_local(_ACP, session_state_root=root)
    assert snap["ok"] is True
    return snap["presence"]


_TURN = [_ev("session.start", 0), _ev("user.message", 1, content="go", agentMode="interactive"),
         _ev("assistant.turn_start", 2, turnId="1")]


@pytest.mark.parametrize("tail, state", [
    ([], "busy"),
    ([_ev("tool.execution_start", 3), _ev("assistant.message", 4, content="working")], "busy"),
    ([_ev("assistant.turn_end", 3, turnId="1")], "awaiting_input"),
    ([_ev("permission.requested", 3, requestId="r1", agentMode="interactive")], "awaiting_input"),
    ([_ev("permission.requested", 3, requestId="r1"), _ev("permission.completed", 4, requestId="r1")], "busy"),
    ([_ev("assistant.turn_end", 3), _ev("session.shutdown", 4, shutdownType="routine")], "absent"),
])
def test_presence_follows_the_transcript(tmp_path, tail, state):
    assert _presence(tmp_path, _TURN + tail)["state"] == state


def test_an_ended_turn_is_idle_in_autopilot_and_awaiting_input_when_interactive(tmp_path):
    ended = _ev("assistant.turn_end", 9)
    auto = _presence(tmp_path / "a", [*_TURN, _ev("session.mode_changed", 5, previousMode="interactive",
                                                     newMode="autopilot"), ended])
    assert (auto["state"], auto["mode"]) == ("idle", "autopilot")
    steered = _presence(tmp_path / "b", [_ev("session.start", 0),
                                         _ev("user.message", 1, agentMode="autopilot"), ended])
    assert steered["state"] == "idle"
    plain = _presence(tmp_path / "c", [_ev("session.start", 0), _ev("user.message", 1), ended])
    assert (plain["state"], plain["mode"]) == ("awaiting_input", "interactive")


def test_an_unanswered_permission_outlives_later_events(tmp_path):
    p = _presence(tmp_path, [*_TURN, _ev("permission.requested", 3, requestId="r1"),
                             _ev("assistant.turn_end", 4)])
    assert (p["state"], p["pending_permissions"]) == ("awaiting_input", 1)


def test_only_events_since_the_last_resume_count(tmp_path):
    """A permission left pending before a shutdown and resume is no longer asked."""
    p = _presence(tmp_path, [*_TURN, _ev("permission.requested", 3, requestId="old"),
                             _ev("session.shutdown", 4), _ev("session.resume", 5),
                             _ev("user.message", 6), _ev("assistant.turn_end", 7)])
    assert (p["state"], p["pending_permissions"]) == ("awaiting_input", 0)
    assert p["last_event"] == "assistant.turn_end"


def test_a_partial_last_line_is_unknown(tmp_path):
    p = _presence(tmp_path, _TURN, raw_tail='{"type": "tool.execution_sta')
    assert p["state"] == "unknown" and "partial" in p["reason"]


def test_a_unicode_line_separator_inside_a_record_is_not_a_record_break(tmp_path):
    """JSONL records end at ``\\n`` only: a raw U+2028/U+2029 in a valid final message
    is part of that record, not a malformed fragment -- and non-ASCII text (U+2603)
    survives the driver's stdout whatever its encoding."""
    line = json.dumps(_ev("assistant.message", 3, content="a\u2028b\u2029c \u2603"), ensure_ascii=False)
    p = _presence(tmp_path, _TURN, raw_tail=line + "\n")
    assert p["state"] == "busy", p


def test_a_sub_agent_turn_end_does_not_settle_the_session(tmp_path):
    """A nested ``assistant.turn_end`` (``data.agentId``) leaves the parent turn busy;
    only the parent's own turn end settles it."""
    nested = [_ev("tool.execution_start", 3, agentId="sub-1"),
              _ev("assistant.turn_end", 4, turnId="s1", agentId="sub-1")]
    p = _presence(tmp_path / "a", _TURN + nested)
    assert (p["state"], p["last_event"]) == ("busy", "assistant.turn_end")
    p = _presence(tmp_path / "b", _TURN + nested + [_ev("assistant.turn_end", 5, turnId="1")])
    assert p["state"] == "awaiting_input"


def test_the_top_level_json_flag_reaches_presence():
    """``agent-bridge --json presence <t>`` stays JSON: the subcommand's own ``--json``
    must not overwrite the top-level flag with its default."""
    from agent_bridge import __main__ as m

    parser = m.build_parser()
    assert parser.parse_args(["--json", "presence", "x"]).json is True
    assert parser.parse_args(["presence", "x", "--json"]).json is True
    assert parser.parse_args(["presence", "x"]).json is False


def test_no_presence_bearing_event_is_unknown(tmp_path):
    p = _presence(tmp_path, [_ev("session.start", 0), _ev("session.model_change", 1)])
    assert p["state"] == "unknown"
    assert p["confidence"] == "scanned"


def test_the_shipped_driver_classifies_the_same_on_a_remote_shell(tmp_path):
    """The codespace path runs the same driver through bash: its presence matches."""
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if not bash or os.name == "nt":
        pytest.skip("needs a POSIX bash")
    root = str(tmp_path)
    _write_session(root, _ACP, _TURN + [_ev("assistant.turn_end", 3)])
    out = subprocess.run([bash, "-c", ps.build_peek_command(_ACP, session_state_root=root)],
                         capture_output=True, text=True, timeout=60).stdout
    assert ps.parse_peek_result(out)["presence"]["state"] == "awaiting_input"


def test_a_mode_signal_older_than_the_parsed_tail_still_counts(tmp_path):
    """A long autopilot turn pushes its user.message (with agentMode) out of the parsed
    tail; the mode is still found in the read window, back to the last boundary."""
    root = str(tmp_path)
    tools = [_ev("tool.execution_complete", 10 + i) for i in range(30)]
    _write_session(root, _ACP, [_ev("session.start", 0), _ev("user.message", 1, agentMode="autopilot"),
                                *tools, _ev("assistant.turn_end", 59)])
    p = ps.snapshot_local(_ACP, session_state_root=root, tail_lines=5)["presence"]
    assert (p["state"], p["mode"], p["confidence"]) == ("idle", "autopilot", "scanned")


def test_an_ended_turn_with_no_mode_signal_is_a_heuristic(tmp_path):
    p = _presence(tmp_path, [_ev("session.start", 0), _ev("assistant.turn_start", 1),
                             _ev("assistant.turn_end", 2)])
    assert (p["state"], p["confidence"]) == ("awaiting_input", "heuristic")


def test_a_permission_request_older_than_the_parsed_tail_still_awaits_input(tmp_path):
    """Hundreds of events after an unanswered permission request: it still awaits."""
    root = str(tmp_path)
    later = [_ev("tool.execution_complete", 10 + i % 40) for i in range(60)]
    _write_session(root, _ACP, [*_TURN, _ev("permission.requested", 3, requestId="r1"), *later])
    p = ps.snapshot_local(_ACP, session_state_root=root, tail_lines=5)["presence"]
    assert (p["state"], p["pending_permissions"]) == ("awaiting_input", 1)


def test_an_event_name_as_a_payload_value_is_not_that_event(tmp_path):
    """The mode comes from beyond the parsed tail, past an event whose payload value is
    literally ``session.start``: only an event's own ``type`` is a session boundary."""
    root = str(tmp_path)
    quoting = _ev("session.info", 4, message="session.start")  # a value that is exactly an event name
    _write_session(root, _ACP, [*_TURN, _ev("session.mode_changed", 3, newMode="autopilot"), quoting,
                                _ev("assistant.turn_end", 5)])
    p = ps.snapshot_local(_ACP, session_state_root=root, tail_lines=2)["presence"]
    assert (p["state"], p["mode"]) == ("idle", "autopilot")


def test_a_permission_request_beyond_the_byte_window_still_awaits_input(tmp_path):
    """A 2.4 MB transcript: an unanswered request near the start, then megabytes of tool
    events. The scan reads back to the session boundary, not a fixed byte tail."""
    root = str(tmp_path)
    filler = [_ev("tool.execution_complete", 10 + i % 40, output="x" * 2000) for i in range(1200)]
    _write_session(root, _ACP, [*_TURN, _ev("permission.requested", 3, requestId="r1",
                                             agentMode="interactive"), *filler])
    assert os.path.getsize(os.path.join(root, _ACP, "events.jsonl")) > 2_000_000
    p = ps.snapshot_local(_ACP, session_state_root=root)["presence"]
    assert (p["state"], p["pending_permissions"], p["mode"]) == ("awaiting_input", 1, "interactive")
    assert p["confidence"] == "scanned"


def test_no_boundary_within_the_scan_cap_is_unknown(tmp_path, monkeypatch):
    """The boundary and an unanswered request lie beyond the scan cap: the events since
    the boundary aren't all known, so presence is unknown rather than a guess."""
    monkeypatch.setenv("AGENT_BRIDGE_PRESENCE_SCAN_CAP", str(64 * 1024))
    root = str(tmp_path)
    filler = [_ev("tool.execution_complete", 10 + i % 40, output="x" * 2000) for i in range(100)]
    _write_session(root, _ACP, [*_TURN, _ev("permission.requested", 3, requestId="r1"), *filler])
    p = ps.snapshot_local(_ACP, session_state_root=root)["presence"]
    assert p["state"] == "unknown" and "no session start or resume" in p["reason"]


def test_a_transcript_without_any_session_boundary_is_unknown(tmp_path):
    """A small, truncated transcript with no session start or resume: what came before
    is unknown, so presence is too -- whatever the file's size."""
    p = _presence(tmp_path, [_ev("permission.completed", 1, requestId="r0"),
                             _ev("tool.execution_complete", 2)])
    assert p["state"] == "unknown" and "no session start or resume" in p["reason"]


def _usage(tmp_path, events):
    root = str(tmp_path)
    _write_session(root, _ACP, events)
    snap = ps.snapshot_local(_ACP, session_state_root=root)
    assert snap["ok"] is True
    return snap["usage"]


def test_usage_is_the_newest_report_even_before_a_resume(tmp_path):
    """Copilot's totals are cumulative over the session's life: a checkpoint written
    before the latest resume is still the answer; a checkpoint carries no tokens."""
    u = _usage(tmp_path, [*_events(resumed=True), _ev("user.message", 9, content="more")])
    assert u["reported"] is True and u["source"] == "checkpoint"
    assert (u["premium_requests"], u["nano_aiu"], u["tokens"]) == (2, 11346730000, None)


def test_usage_at_shutdown_carries_tokens_and_each_figure_is_its_newest_report(tmp_path):
    """A shutdown that doesn't carry a figure (an unreadable AIU here) leaves the
    newest checkpoint's; a figure nothing reports stays None, never zero."""
    shutdown = {"type": "session.shutdown", "timestamp": "2026-08-14T00:09:00Z", "data": {
        "shutdownType": "routine", "totalPremiumRequests": 7.5, "totalNanoAiu": "lots",
        "tokenDetails": {"input": {"tokenCount": 10}, "output": {"tokenCount": 20},
                         "cache_read": {"tokenCount": 30}}}}
    u = _usage(tmp_path / "a", [*_events(), shutdown])
    assert (u["source"], u["premium_requests"], u["nano_aiu"]) == ("shutdown", 7.5, 11346730000)
    assert u["tokens"] == {"input": 10, "output": 20, "cache_read": 30, "cache_write": None}
    only = {"type": "session.usage_checkpoint", "timestamp": "t", "data": {"totalPremiumRequests": 1}}
    u = _usage(tmp_path / "b", [_ev("session.start", 0), only])
    assert (u["premium_requests"], u["nano_aiu"], u["tokens"]) == (1, None, None)


def test_a_session_that_never_reported_usage_is_not_reported_never_zero(tmp_path):
    u = _usage(tmp_path, [_ev("session.start", 0), _ev("user.message", 1, content="hi")])
    assert u["reported"] is False and "premium_requests" not in u and "no usage" in u["reason"]


def test_usage_cli_rolls_up_only_what_was_reported(monkeypatch, capsys):
    """Two sessions reported, one didn't, one can't be found: the totals sum the
    reports and say how many sessions they cover; nothing unreported reads as zero."""
    import argparse

    from agent_bridge import __main__ as core
    from agent_bridge import session_maintenance_cli as cli

    snaps = {
        "a": {"ok": True, "usage": {"reported": True, "premium_requests": 3, "nano_aiu": 10,
                                    "tokens": None, "reported_at": "t", "source": "checkpoint"}},
        "b": {"ok": True, "usage": {"reported": True, "premium_requests": 4.5, "nano_aiu": None,
                                    "tokens": None, "reported_at": "t", "source": "checkpoint"}},
        "c": {"ok": True, "usage": {"reported": False, "reason": "no usage checkpoint"}},
    }

    def peek(args):
        if args.target not in snaps:
            raise SystemExit(1)
        return {}, f"sid-{args.target}", args.target, "acp", snaps[args.target]

    monkeypatch.setattr(cli, "_peek_target", peek)
    out = {}
    monkeypatch.setattr(core, "_json_out", lambda obj: out.update(obj))
    cli._cmd_usage(argparse.Namespace(targets=["a", "b", "c", "gone"], json=True))
    assert out["rollup"]["premium_requests"] == {"value": 7.5, "coverage": "2/4"}
    assert out["rollup"]["nano_aiu"] == {"value": 10, "coverage": "1/4"}
    assert [r["usage"]["reported"] for r in out["sessions"]] == [True, True, False, False]
    parser = core.build_parser()
    assert parser.parse_args(["--json", "usage", "x"]).json is True
    assert parser.parse_args(["usage", "x", "y"]).targets == ["x", "y"]

