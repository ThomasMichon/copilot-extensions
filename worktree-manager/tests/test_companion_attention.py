"""Optional head-attention parsing and real selected-engine process boundary."""

import json
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from worktree_manager import companion_attention as ca
from worktree_manager import engine_client as ec
from worktree_manager.companion_client import cancellable_read_runner


def envelope(rest="awaiting-operator"):
    return {
        "contract": ca.CONTRACT, "version": 1, "supported_versions": [1],
        "capabilities": ["last_reported_root_attention"], "session_id": "session-a",
        "availability": "observed", "as_of": "2026-10-25T09:00:00Z",
        "source": {"kind": "copilot.substatus.json", "max_bytes": 65536},
        "evidence_scope": "last_reported_root_state", "runtime_continuity": "unknown",
        "rest": rest, "reported_at": "2020-01-02T03:04:05+00:00",
        "reported_at_source": "restAt", "issues": {},
    }


def head():
    return {"worktree_id": "selected", "tracked": True, "active": True,
            "head_session": "session-a"}


def test_exact_commands_and_bound_timeouts(monkeypatch):
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["fixture-engine"])
    calls = []

    def run(command, timeout):
        calls.append((command, timeout))
        return SimpleNamespace(
            returncode=0, stderr="", stdout=json.dumps(head() if len(calls) == 1 else envelope()))

    result = ca.read_session_attention("fixture", "selected", "session-a", runner=run)
    assert result == {"attention": envelope(), "error": None}
    assert calls == [
        (["fixture-engine", "--project", "fixture", "head-session",
          "--worktree", "selected", "--json"], 5),
        (["fixture-engine", "--project", "fixture", "session-attention", "session-a", "--json"], 5),
    ]


@pytest.mark.parametrize("available", [True, False])
def test_real_selected_engine_subprocess_and_rollback(tmp_path, available):
    target = tmp_path / "selected checkout"
    target.mkdir()
    script = tmp_path / "engine.py"
    script.write_text(
        "import os,sys\n"
        f"assert os.path.realpath(os.getcwd()) == {str(target.resolve())!r}\n"
        "if sys.argv[1:] == ['head-session','--worktree','selected','--json']:\n"
        f"    print({json.dumps(head())!r})\n"
        "else:\n"
        "    assert sys.argv[1:] == ['session-attention','session-a','--json']\n"
        + (f"    print({json.dumps(envelope())!r})\n" if available else
           "    print('unrecognized command session-attention', file=sys.stderr)\n"
           "    raise SystemExit(2)\n"), encoding="utf-8",
    )
    ec.set_engine_command([sys.executable, str(script)])
    try:
        result = ca.read_session_attention(None, "selected", "session-a",
                                          runner=cancellable_read_runner(Event(), cwd=str(target)))
    finally:
        ec.set_engine_command(None)
    assert result == ({"attention": envelope(), "error": None} if available else {
        "attention": None, "error": "attention-read-failed",
    })


@pytest.mark.parametrize("change,error", [
    ({"head_session": "successor"}, "head-changed-refresh-required"),
    ({"worktree_id": "other"}, "head-session-unavailable"),
    ({"tracked": False}, "head-session-unavailable"),
    ({"active": False}, "head-session-unavailable"),
])
def test_changed_or_unavailable_head_never_reads_a_pulse(monkeypatch, change, error):
    value, calls = head(), []
    value.update(change)

    def read(*args, **kwargs):
        calls.append(args[1][0])
        assert calls == ["head-session"]
        return value

    monkeypatch.setattr(ec, "run_json", read)
    assert ca.read_session_attention(None, "selected", "session-a") == {
        "attention": None, "error": error,
    }


@pytest.mark.parametrize("key,bad", [
    ("contract", "other"), ("version", True), ("version", 2),
    ("supported_versions", [True]), ("supported_versions", [2]),
    ("capabilities", []), ("session_id", "other"), ("availability", "current"),
    ("as_of", "not-time"), ("source", {"kind": "transcript", "max_bytes": 65536}),
    ("evidence_scope", "current"), ("runtime_continuity", "live"),
    ("rest", None), ("rest", []), ("reported_at", "2020-01-02"),
    ("reported_at_source", "updatedAt"), ("issues", {"private-error": "raw"}),
])
def test_malformed_or_unsupported_evidence_is_unknown(key, bad):
    value = envelope()
    value[key] = bad
    assert ca.attention_error(value, "session-a")


@pytest.mark.parametrize("key", ["rest", "reported_at", "reported_at_source"])
def test_missing_required_key_is_not_nullable(key):
    value = envelope("busy")
    value.update(reported_at=None, reported_at_source=None)
    value.pop(key)
    assert ca.attention_error(value, "session-a") == "malformed-attention"


def test_unknown_initial_busy_and_additive_fields():
    value = envelope("busy")
    value.update(reported_at=None, reported_at_source=None, future_field={"additive": True})
    assert ca.attention_error(value, "session-a") is None
    value.update(availability="unknown", rest=None)
    assert ca.attention_error(value, "session-a") is None
    value["rest"] = "busy"
    assert ca.attention_error(value, "session-a") == "malformed-attention"


def test_cancel_and_private_error_are_not_conflated(monkeypatch):
    def fail(*a, **kw):
        raise ec.EngineError("private-path / private-message")

    monkeypatch.setattr(ec, "run_json", fail)
    assert ca.read_session_attention(None, "selected", "session-a") == {
        "attention": None, "error": "attention-read-failed",
    }

    def cancel(*a, **kw):
        raise InterruptedError

    monkeypatch.setattr(ec, "run_json", cancel)
    with pytest.raises(InterruptedError):
        ca.read_session_attention(None, "selected", "session-a")


@pytest.mark.parametrize("rest", ["busy", "idle", "awaiting-operator"])
def test_render_is_attributed_last_report_not_current_input(rest):
    text = ca.attention_text(envelope(rest), None).plain
    assert f"last reported: {rest}" in text
    assert "2020-01-02T03:04:05+00:00" in text and "continuity unknown" in text
