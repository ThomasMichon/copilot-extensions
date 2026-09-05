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
        "target_type": "container",
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
