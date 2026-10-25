"""Metadata client controls with a synthetic engine runner; no live sessions."""

import json
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from worktree_manager import companion_observations as observations
from worktree_manager import engine_client as ec
from worktree_manager.companion_client import cancellable_read_runner


def envelope():
    return {
        "contract": observations.CONTRACT, "version": 1, "supported_versions": [1],
        "session_id": "session-a", "capabilities": ["tools", "subagents"],
        "availability": "available", "as_of": "2026-10-25T07:00:00Z",
        "source": {"kind": "copilot.events.jsonl", "snapshot_bytes": 240, "observed_bytes": 240},
        "history": {"scope": "retained_file", "complete": True,
                    "session_start_records": 1, "valid_records": 5},
        "issues": {},
        "tools": {"started_records": 2, "completed_records": 1,
                  "by_name": {"read": {"started_records": 2, "completed_records": 1}},
                  "unattributed_records": {"started": 0, "completed": 0},
                  "conflicting_tool_call_ids": 0},
        "subagents": {
            "availability": "observed", "runtime_continuity": "unknown",
            "lifecycle_records": {"started": 1, "completed": 1, "failed": 0},
            "unattributed_records": 0,
            "agents": [{
                "agent_id": "actual-agent", "last_reported_lifecycle": "completed",
                "parent_tool_call_id": "call-not-agent", "name": "task",
                "display_name": "Worker", "model": None,
                "reported_duration_ms": 12, "reported_tool_calls": None,
                "lifecycle_records": 2,
            }],
        },
        "pending_input": {"availability": "unknown"},
    }


def test_actual_json_boundary_and_exact_command(monkeypatch):
    monkeypatch.setattr(ec, "engine_base_command", lambda: ["synthetic-engine"])
    calls = []
    value = envelope()

    def runner(command, timeout):
        calls.append((command, timeout))
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")

    result = observations.read_session_observations("project-a", "session-a", runner=runner)
    assert result == {"observation": value, "error": None}
    assert calls == [(["synthetic-engine", "--project", "project-a",
                      "session-observations", "session-a", "--json"], 120)]


@pytest.mark.parametrize("available", [True, False])
def test_real_engine_subprocess_uses_selected_cwd_and_provider_shape(tmp_path, available):
    target = tmp_path / "selected checkout"
    target.mkdir()
    value = envelope()
    if not available:
        value.update(availability="unavailable", tools=None, subagents=None)
        value["source"].update(snapshot_bytes=None, observed_bytes=0)
        value["history"].update(complete=False, session_start_records=0, valid_records=0)
        value["issues"] = {"session_unavailable": 1}
    script = tmp_path / "fixture_engine.py"
    script.write_text(
        "import json,os,sys\n"
        f"assert os.path.realpath(os.getcwd()) == {str(target.resolve())!r}\n"
        "assert sys.argv[1:] == ['session-observations','session-a','--json']\n"
        f"print({json.dumps(value)!r})\n",
        encoding="utf-8",
    )
    ec.set_engine_command([sys.executable, str(script)])
    try:
        result = observations.read_session_observations(
            None, "session-a", runner=cancellable_read_runner(Event(), cwd=str(target)),
        )
        assert result["observation"] == value
        assert result["error"] == (None if available else "provider-unavailable")
    finally:
        ec.set_engine_command(None)


@pytest.mark.parametrize("availability", ["available", "partial", "unavailable"])
def test_availability_and_nullable_optional_data(monkeypatch, availability):
    value = envelope()
    value.update(availability=availability, tools=None, subagents=None)
    value["issues"] = {"read_error": 1}
    value["source"]["observed_bytes"] = 180
    value["history"]["complete"] = False
    monkeypatch.setattr(ec, "run_json", lambda *a, **kw: value)
    result = observations.read_session_observations(None, "session-a")
    assert result["observation"] is value
    assert result["error"] == ("provider-unavailable" if availability == "unavailable" else None)
    assert value["source"]["observed_bytes"] == 180


def test_unavailable_provider_without_snapshot_retains_its_envelope(monkeypatch):
    value = envelope()
    value.update(availability="unavailable", tools=None, subagents=None)
    value["source"].update(snapshot_bytes=None, observed_bytes=0)
    value["issues"] = {"session_unavailable": 1}
    value["history"].update(complete=False, valid_records=0, session_start_records=0)
    monkeypatch.setattr(ec, "run_json", lambda *a, **kw: value)
    assert observations.read_session_observations(None, "session-a") == {
        "observation": value, "error": "provider-unavailable",
    }


def test_missing_snapshot_field_is_not_a_nullable_snapshot(monkeypatch):
    value = envelope()
    value["source"].pop("snapshot_bytes")
    monkeypatch.setattr(ec, "run_json", lambda *a, **kw: value)
    assert observations.read_session_observations(None, "session-a") == {
        "observation": None, "error": "malformed-observation",
    }


@pytest.mark.parametrize(("path", "bad"), [
    (("contract",), "other-provider"),
    (("version",), True), (("version",), 2),
    (("supported_versions",), [True]), (("supported_versions",), [2]),
    (("session_id",), "different-session"),
    (("capabilities",), "tools"), (("availability",), "healthy"),
    (("as_of",), None), (("as_of",), float("nan")),
    (("source", "kind"), "transcript"),
    (("source", "snapshot_bytes"), True),
    (("history", "scope"), "lifetime"),
    (("history", "complete"), "yes"),
    (("history", "valid_records"), -1),
    (("issues",), {"raw": "exception text"}),
    (("tools", "started_records"), "2"),
    (("tools", "unattributed_records"), 0),
    (("tools", "by_name"), {"read": {"started_records": 1}}),
    (("tools", "conflicting_tool_call_ids"), []),
    (("subagents", "runtime_continuity"), "running"),
    (("subagents", "unattributed_records"), {}),
    (("subagents", "agents"), ["not-an-agent"]),
    (("pending_input", "availability"), "waiting"),
])
def test_malformed_or_unsupported_metadata_is_unknown(monkeypatch, path, bad):
    value = envelope()
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad
    monkeypatch.setattr(ec, "run_json", lambda *a, **kw: value)
    result = observations.read_session_observations(None, "session-a")
    assert result["observation"] is None
    assert result["error"]


@pytest.mark.parametrize(("field", "bad"), [
    ("agent_id", None), ("last_reported_lifecycle", "retired"),
    ("parent_tool_call_id", {"agentId": "forged"}),
    ("name", []), ("display_name", False), ("model", {}),
    ("reported_duration_ms", float("inf")), ("reported_tool_calls", True),
    ("lifecycle_records", {"started": 1}),
])
def test_agent_metadata_types(monkeypatch, field, bad):
    value = envelope()
    value["subagents"]["agents"][0][field] = bad
    monkeypatch.setattr(ec, "run_json", lambda *a, **kw: value)
    assert observations.read_session_observations(None, "session-a")["observation"] is None


@pytest.mark.parametrize("value", [None, [], {}, {"error": "unknown command"}])
def test_non_contract_responses_do_not_raise(monkeypatch, value):
    monkeypatch.setattr(ec, "run_json", lambda *a, **kw: value)
    assert observations.read_session_observations(None, "session-a") == {
        "observation": None, "error": "malformed-observation",
    }


@pytest.mark.parametrize("error", [ec.EngineError, ec.EngineFeatureUnavailable])
def test_read_failure_is_sanitized_not_empty_agents(monkeypatch, error):
    def fail(*a, **kw):
        raise error("private stderr / secret path")

    monkeypatch.setattr(ec, "run_json", fail)
    assert observations.read_session_observations(None, "session-a") == {
        "observation": None, "error": "observation-read-failed",
    }


def test_cancellation_is_not_swallowed(monkeypatch):
    def cancelled(*a, **kw):
        raise InterruptedError("cancelled")

    monkeypatch.setattr(ec, "run_json", cancelled)
    with pytest.raises(InterruptedError):
        observations.read_session_observations(None, "session-a")


def test_no_fallback_and_invalid_id_never_runs(monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("must not read engine for invalid selection")

    monkeypatch.setattr(ec, "run_json", forbidden)
    for sid in (None, "", "  ", [], {}):
        assert observations.read_session_observations(None, sid)["error"] == "invalid-session-id"


def test_bounded_collections_and_cycles():
    value = envelope()
    value["subagents"]["agents"] = [value] * (observations.MAX_RECORDS + 1)
    assert observations.observation_error(value) == "malformed-observation"
    value = envelope()
    value["tools"]["by_name"]["cycle"] = value["tools"]
    assert observations.observation_error(value) is None  # known scalar fields only
    observations.observation_error(value)
    assert value["tools"]["by_name"]["cycle"] is value["tools"]


def test_additive_metadata_and_capability_declarations():
    value = envelope()
    value["future_field"] = {"anything": True}
    value["capabilities"] = {"tools": True, "subagents": True}
    assert observations.observation_error(value) is None
