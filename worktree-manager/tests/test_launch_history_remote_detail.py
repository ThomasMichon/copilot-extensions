"""Exact configured-source reads never widen private-input or replay authority."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json

import pytest
from machine_transport import MachineEntry, SSHEnvironment

from worktree_manager import engine_client, launch_history_cli as history, machine_roster
from worktree_manager import launch_history_remote_detail as remote, launch_history_sweep as sweep
from worktree_manager.launch_attempts import LaunchAttempts

ATTEMPT = "a" * 32
PROMPT = "  private synthetic input\nUnicode: \u03bb; '$HOME'  "


def arguments(**changes):
    return argparse.Namespace(**{
        "attempt_id": ATTEMPT, "sweep_project": "example", "source_machine": ["worker-linux"],
        "include_input": False, "summary": False, "limit": 100, "event_limit": 100,
        "after_sequence": 0, "receipt_limit": 100, "receipt_after": None,
        **{field: None for field in sweep._FILTERS}, **changes,
    })


def entry():
    return MachineEntry(
        key="worker-linux", display_name="Synthetic worker", environment="linux",
        physical_host="worker", execution_platform="linux", ssh_ready=True,
        ssh_environments=[SSHEnvironment("linux", "worker-profile", "bash")],
    )


@pytest.fixture
def saved(tmp_path, monkeypatch):
    store = LaunchAttempts(tmp_path)
    store.record_intent(
        ATTEMPT, action="new", project="example", surface="synthetic-detail", is_local=False,
        prompt=PROMPT, options={"no_mux": True, "nested": {"literal": "$(not-executed)"}},
    )
    store.append(ATTEMPT, owner="manager", phase="launcher-returned", exit_code=0)
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **kw: {
        "schema": history._SCHEMA, "version": 1, "scope": "local-project",
        "attempt_id": ATTEMPT, "receipts": [], "admissions": [], "errors": [],
        "has_more": False, "next_after": None, "prompt_acceptance": "unknown",
    })
    monkeypatch.setattr(machine_roster, "selected_entries", lambda _: {"worker-linux": entry()})
    return store


def response(store, **kwargs):
    return history.read_detail(store, ATTEMPT, **kwargs)


def test_cli_routes_one_exact_source_without_reading_host_journal_or_engine(saved, monkeypatch, capsys):
    value = response(saved)
    monkeypatch.setattr(history, "LaunchAttempts", lambda: pytest.fail("host journal opened"))
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **kw: pytest.fail("host engine called"))
    calls = []
    async def read(target, query, **kwargs):
        calls.append((target.key, query, kwargs))
        return json.dumps(value).encode(), 0
    monkeypatch.setattr(sweep, "query_source_result", read)
    assert history.main([
        ATTEMPT, "--sweep-project", "example", "--source-machine", "worker-linux", "--json",
    ]) == 0
    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert report["scope"] == "selected-configured-ssh-manager" and not report["atomic"]
    source = report["source"]
    assert source["machine"] == "worker-linux" and source["environment"] == "linux"
    assert source["detail"]["attempt"]["attempt_id"] == ATTEMPT
    assert "input" not in source["detail"]["attempt"] and "private synthetic" not in raw
    assert source["detail"]["engine"]["absence"] == "unknown"
    assert "receipts" not in source["detail"]["engine"]
    assert calls == [("worker-linux", [
        "launch-attempts", ATTEMPT, "--json", "--event-limit", "100", "--after-sequence", "0",
        "--receipt-limit", "100",
    ], {"timeout": sweep.SOURCE_TIMEOUT})]


def test_explicit_input_preserves_exact_text_and_options(saved):
    args = arguments(include_input=True)
    value = response(saved, include_input=True)
    async def read(*a, **kw):
        assert "--include-input" in a[1]
        return json.dumps(value).encode(), 0
    report = asyncio.run(remote.read_remote_detail(args, read_source=read))
    assert report["source"]["status"] == "read"
    assert report["source"]["detail"]["attempt"]["input"] == {
        "prompt": PROMPT, "options": {"no_mux": True, "nested": {"literal": "$(not-executed)"}},
    }
    assert report["prompt_acceptance"] == "unknown"


@pytest.mark.parametrize("changes", [
    {"attempt_id": "foreign"}, {"attempt_id": None}, {"sweep_project": None},
    {"source_machine": []}, {"source_machine": ["one", "two"]}, {"source_machine": ["\0"]},
    {"summary": True}, {"project": "journal-filter"}, {"action": "new"}, {"limit": 1},
    {"before": 1}, {"before_id": "b" * 32}, {"after": 0}, {"outcome": "returned"},
    {"event_limit": 0}, {"event_limit": 1001}, {"after_sequence": -1},
    {"receipt_limit": 0}, {"receipt_limit": 101}, {"receipt_after": "../private"},
])
def test_invalid_detail_refuses_before_configuration_or_transport(monkeypatch, changes):
    monkeypatch.setattr(machine_roster, "selected_entries", lambda *a: pytest.fail("roster read"))
    with pytest.raises(sweep.SweepError, match="invalid-query"):
        asyncio.run(remote.read_remote_detail(arguments(**changes)))


def test_partial_engine_keeps_explicit_private_detail_and_nonzero_target_exit(saved, monkeypatch, capsys):
    def unavailable(*a, **kw):
        raise engine_client.EngineError("private engine stderr")
    monkeypatch.setattr(engine_client, "run_json", unavailable)
    value = response(saved, include_input=True)
    async def read(*a, **kw):
        return json.dumps(value).encode(), 1
    monkeypatch.setattr(sweep, "query_source_result", read)
    assert history.main([
        ATTEMPT, "--sweep-project", "example", "--source-machine", "worker-linux",
        "--include-input", "--json",
    ]) == 1
    raw = capsys.readouterr().out
    source = json.loads(raw)["source"]
    assert source["status"] == "partial" and source["target_exit_code"] == 1
    assert source["detail"]["attempt"]["input"]["prompt"] == PROMPT
    assert source["detail"]["engine"]["status"] == "unavailable"
    assert "private engine stderr" not in raw


def test_nonzero_after_valid_detail_is_not_promoted_to_success(saved):
    value = response(saved)
    async def read(*a, **kw):
        return json.dumps(value).encode(), 1
    report = asyncio.run(remote.read_remote_detail(arguments(), read_source=read))
    assert report["source"]["status"] == "partial"
    assert report["source"]["code"] == "incomplete-evidence"
    assert report["source"]["detail"]["attempt"]["attempt_id"] == ATTEMPT


@pytest.mark.parametrize("change", [
    lambda p: p.update(input="private unexpected field"),
    lambda p: p.update(version=True),
    lambda p: p["attempt"].update(attempt_id="b" * 32),
    lambda p: p["attempt"].update(input={"prompt": PROMPT, "options": {}}),
    lambda p: p["attempt"]["events"][0]["details"].update(input=PROMPT),
    lambda p: p["attempt"]["events"][0].update(observed_at=True),
    lambda p: p["attempt"]["events"][0].update(sequence=0),
    lambda p: p["attempt"].update(next_event_sequence=1),
    lambda p: p["attempt"].update(events_has_more=True),
    lambda p: p["engine"].update(input=PROMPT),
    lambda p: p["engine"].update(attempt_id="b" * 32),
    lambda p: p["engine"].update(next_after="../private"),
])
def test_unexpected_input_and_foreign_or_malformed_detail_never_echo(saved, change):
    value = response(saved)
    change(value)
    with pytest.raises(sweep.SweepError, match="incompatible-response"):
        remote.validate_detail(json.dumps(value).encode(), arguments())


def test_private_opt_in_requires_a_complete_available_input(saved):
    value = response(saved, include_input=True)
    for change in (
        lambda p: p["attempt"].pop("input"),
        lambda p: p["attempt"].update(inputs_available=False),
        lambda p: p["attempt"]["input"].update(prompt=None),
        lambda p: p["attempt"]["input"].update(options=[]),
    ):
        changed = copy.deepcopy(value)
        change(changed)
        with pytest.raises(sweep.SweepError, match="incompatible-response"):
            remote.validate_detail(json.dumps(changed).encode(), arguments(include_input=True))


def test_missing_or_expired_input_returns_only_sanitized_unavailability(saved, capsys, monkeypatch):
    value = {
        "schema": "worktree-manager-launch-history", "version": 1, "scope": "local-manager",
        "errors": [{"source": "manager", "code": "unreadable-or-expired"}], "error": PROMPT,
    }
    async def read(*a, **kw):
        return json.dumps(value).encode(), 1
    monkeypatch.setattr(sweep, "query_source_result", read)
    assert history.main([
        ATTEMPT, "--sweep-project", "example", "--source-machine", "worker-linux",
        "--include-input", "--json",
    ]) == 1
    raw = capsys.readouterr().out
    assert "private synthetic" not in raw and '"detail"' not in raw
    assert json.loads(raw)["source"]["code"] == "unreadable-or-expired"


def test_pagination_is_forwarded_to_existing_target_owners(saved):
    value = response(saved, event_limit=1, receipt_limit=2)
    args = arguments(event_limit=1, receipt_limit=2)
    detail, partial = remote.validate_detail(json.dumps(value).encode(), args)
    assert detail["attempt"]["events_has_more"] and not partial
    args.after_sequence = detail["attempt"]["next_event_sequence"]
    args.receipt_after = "b" * 32 + ".execution.json"
    query = remote.query_argv(args)
    assert query[query.index("--after-sequence") + 1] == str(args.after_sequence)
    assert query[-2:] == ["--receipt-after", args.receipt_after]
    with pytest.raises(sweep.SweepError, match="incompatible-response"):
        remote.validate_detail(json.dumps(value).encode(), args)


def test_raw_engine_rows_are_projected_not_echoed(saved):
    value = response(saved)
    value["engine"]["receipts"] = [{
        "source": "resolution", "operation_id": "b" * 32, "evidence": {
            "schema": "worktree-launch-resolution", "version": 1,
            "attempt_id": ATTEMPT, "operation_id": "b" * 32, "input": PROMPT,
            "events": [{"at": "2026-10-25T00:00:00+00:00", "phase": "resolution-requested",
                        "details": {}}],
        },
    }]
    detail, partial = remote.validate_detail(json.dumps(value).encode(), arguments())
    assert not partial and "private synthetic" not in json.dumps(detail)
    assert detail["engine"]["owner_observations"]["resolution"][0]["terminal_phase"] is None
    value["engine"]["receipts"][0]["evidence"]["operation_id"] = "c" * 32
    detail, partial = remote.validate_detail(json.dumps(value).encode(), arguments())
    assert partial and detail["engine"]["errors"] == [{"source": "engine", "code": "invalid-receipt"}]
    assert "private synthetic" not in json.dumps(detail)


@pytest.mark.parametrize("output", [
    b'{"schema":1,"schema":2}', b'{"unknown":NaN}', b"[" * 2000,
])
def test_ambiguous_or_deep_json_is_sanitized(output):
    with pytest.raises(sweep.SweepError, match="incompatible-response"):
        remote.validate_detail(output, arguments())


def test_response_budget_precedes_parsing():
    with pytest.raises(sweep.SweepError, match="response-too-large"):
        remote.validate_detail(b"x" * (sweep.MAX_RESPONSE_BYTES + 1), arguments())


@pytest.mark.parametrize("exit_code", [2, 127, True])
def test_legacy_or_failed_reader_does_not_expose_process_output(saved, exit_code):
    async def read(*a, **kw):
        return PROMPT.encode(), exit_code
    report = asyncio.run(remote.read_remote_detail(arguments(), read_source=read))
    assert report["source"]["code"] == "target-reader-unavailable"
    assert "private synthetic" not in json.dumps(report)


@pytest.mark.parametrize("configured", [False, True])
def test_unavailable_roster_or_unconfigured_selection_never_uses_local_fallback(monkeypatch, configured):
    def roster(*a):
        if not configured:
            raise ValueError(PROMPT)
        return {}
    monkeypatch.setattr(machine_roster, "selected_entries", roster)
    async def read(*a, **kw):
        pytest.fail("transport should not open")
    report = asyncio.run(remote.read_remote_detail(arguments(), read_source=read))
    assert report["source"]["code"] == ("not-configured" if configured else "roster-unavailable")
    assert "private synthetic" not in json.dumps(report) and "detail" not in report["source"]
