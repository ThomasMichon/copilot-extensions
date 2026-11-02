"""Whole-attempt positive evidence stays bounded, local and explicitly incomplete."""

from __future__ import annotations

import copy
import json

import pytest

from worktree_manager import engine_client, launch_history_cli as history
from worktree_manager import launch_history_summary as summary
from worktree_manager.launch_attempts import LaunchAttempts

ATTEMPT = "a" * 32
REQUEST = "b" * 32
AT = "2026-10-25T00:00:00+00:00"


@pytest.fixture
def saved(tmp_path, monkeypatch):
    store = LaunchAttempts(tmp_path)
    store.record_intent(
        ATTEMPT, action="new", project="example", surface="fixture",
        machine="selected-host", is_local=False, prompt=" synthetic private input\n ",
    )
    store.append(ATTEMPT, owner="manager", phase="resolved", request_id=REQUEST)
    monkeypatch.setattr(history, "LaunchAttempts", lambda: store)
    return store


def page(**changes):
    return {
        "schema": history._SCHEMA, "version": 1, "attempt_id": ATTEMPT,
        "scope": "local-project", "receipts": [], "admissions": [], "errors": [],
        "has_more": False, "next_after": None, "prompt_acceptance": "unknown", **changes,
    }


def execution(operation="c" * 32, boundary="backend-command", birth="100"):
    return {
        "source": "execution", "operation_id": operation, "evidence": {
            "schema": "worktree-launch-execution", "version": 1,
            "attempt_id": ATTEMPT, "operation_id": operation, "resolution_id": "d" * 32,
            "parent_id": None, "worktree_id": "example-worktree", "creation_nonce": "nonce",
            "events": [
                {"at": AT, "phase": "execution-requested", "details": {}},
                {"at": AT, "phase": "process-spawned", "details": {
                    "pid": 42, "process_start_time": birth, "boundary": boundary,
                }},
            ],
        },
    }


def native(operation="c" * 32):
    return {
        "source": "native", "operation_id": operation, "evidence": {
            "status": "native-session-observed", "native_session_id": "example-session",
            "source_identity": "example-source", "prompt_acceptance": "unknown",
        },
    }


def admission(**changes):
    return {
        "request_id": REQUEST, "worktree_id": "example-worktree", "seed_id": REQUEST,
        "kind": "new", "creation_started": True, "creation_complete": True,
        "admission_accepted": True, "prompt_acceptance": "unknown", **changes,
    }


def test_summary_reaches_later_pages_and_keeps_qualified_owner_boundaries(saved, monkeypatch, capsys):
    calls = []
    pages = [
        page(receipts=[execution(boundary="launcher-command", birth=None)],
             admissions=[admission()], has_more=True, next_after="c" * 32 + ".execution.json"),
        page(receipts=[native(), execution("e" * 32)], admissions=[admission()]),
    ]
    def run(project, args, **kwargs):
        calls.append((project, args, kwargs))
        return pages[len(calls) - 1]
    monkeypatch.setattr(engine_client, "run_json", run)
    before = {p: p.read_bytes() for p in saved.path.parent.iterdir() if p.is_file()}
    assert history.main([ATTEMPT, "--summary", "--json"]) == 0
    raw = capsys.readouterr().out
    report = json.loads(raw)
    boundaries = report["summary"]["boundaries"]
    assert all(value["status"] == "observed" for value in boundaries.values())
    assert len(boundaries["allocation"]["evidence"]) == 1
    assert boundaries["launcher_start"]["evidence"][0]["process_start_time"] is None
    assert boundaries["backend_start"]["evidence"][0]["operation_id"] == "e" * 32
    assert boundaries["native_session"]["evidence"][0]["operation_id"] == "c" * 32
    assert report["engine"] == {
        "scope": "local-project", "status": "read", "pages_read": 2, "receipts_read": 3,
        "has_more": False, "next_after": None,
    }
    assert report["summary"]["observation"] == "non-atomic"
    assert report["summary"]["absence"] == "unknown"
    assert report["summary"]["selected_remote"] == "not-queried"
    assert report["prompt_acceptance"] == "unknown"
    assert "synthetic private" not in raw
    assert calls[0][0] == "example" and calls[0][1][-2:] == ["--request-id", REQUEST]
    assert calls[1][1][5:7] == ["--after", "c" * 32 + ".execution.json"]
    assert all("--summary" not in args for _, args, _ in calls)
    assert 0 < calls[1][2]["timeout"] <= calls[0][2]["timeout"] <= 120
    assert before == {p: p.read_bytes() for p in saved.path.parent.iterdir() if p.is_file()}


def test_resolution_zero_exit_and_seed_claim_are_not_allocation_or_start(saved, monkeypatch, capsys):
    receipt = execution()
    receipt["evidence"]["events"][-1] = {
        "at": AT, "phase": "seed-claimed",
        "details": {"seed_id": REQUEST, "handoff_id": "f" * 32},
    }
    plan = {
        "source": "resolution", "operation_id": "d" * 32, "evidence": {
            "schema": "worktree-launch-resolution", "version": 1,
            "attempt_id": ATTEMPT, "operation_id": "d" * 32,
            "events": [{"at": AT, "phase": "resolution-requested", "details": {}},
                       {"at": AT, "phase": "plan-ready", "details": {
                "worktree_id": "example-worktree", "exit_code": 0,
            }}],
        },
    }
    saved.append(ATTEMPT, owner="manager", phase="launcher-returned", exit_code=0)
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **kw: page(
        receipts=[receipt, plan], admissions=[admission(kind="resume")],
    ))
    assert history.main([ATTEMPT, "--summary"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["attempt"]["manager_outcome"] == "returned"
    assert all(value == {"status": "unknown", "evidence": []}
               for value in report["summary"]["boundaries"].values())


@pytest.mark.parametrize("arguments", [
    ["--summary"], [ATTEMPT, "--summary", "--receipt-after", "c" * 32 + ".json"],
    [ATTEMPT, "--summary", "--after-sequence", "1"],
    [ATTEMPT, "--summary", "--event-limit", "1"],
    [ATTEMPT, "--summary", "--receipt-limit", "1"],
])
def test_summary_requires_exact_detail_without_page_selectors(saved, monkeypatch, capsys, arguments):
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **kw: pytest.fail("invalid query read"))
    assert history.main([*arguments, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "invalid-query"


def test_partial_failure_preserves_positive_facts_and_sanitizes_errors(saved, monkeypatch, capsys):
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise engine_client.EngineError("private response")
        return page(
            receipts=[execution()], has_more=True, next_after="c" * 32 + ".execution.json",
            errors=[{"source": "native", "operation_id": "d" * 32, "code": "missing"}],
        )
    monkeypatch.setattr(engine_client, "run_json", run)
    assert history.main([ATTEMPT, "--summary", "--json"]) == 1
    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert report["summary"]["boundaries"]["backend_start"]["status"] == "observed"
    assert report["engine"]["has_more"] is True
    assert report["summary"]["errors"] == [
        {"source": "native", "code": "missing", "operation_id": "d" * 32},
        {"source": "engine", "code": "unavailable-or-incompatible"},
    ]
    assert "private response" not in raw


def test_unavailable_summary_keeps_intent_and_unknown_positive_boundaries(saved, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise engine_client.EngineError("private unavailable detail")
    monkeypatch.setattr(engine_client, "run_json", fail)
    assert history.main([ATTEMPT, "--summary", "--json"]) == 1
    raw = capsys.readouterr().out
    result = json.loads(raw)
    assert result["attempt"]["attempt_id"] == ATTEMPT
    assert result["engine"]["status"] == "unavailable" and result["engine"]["pages_read"] == 0
    assert all(value["status"] == "unknown" for value in result["summary"]["boundaries"].values())
    assert "private unavailable" not in raw


@pytest.mark.parametrize("cursor", [None, "../private", "c" * 32 + ".execution.json"])
def test_bad_or_repeated_continuation_stops_without_unbounded_reads(saved, monkeypatch, cursor):
    calls = []
    def read(*args, **kwargs):
        calls.append(kwargs)
        return page(has_more=True, next_after=cursor)
    result = summary.read_summary(saved, ATTEMPT, read_page=read)
    assert len(calls) <= 2
    assert result["summary"]["errors"][-1]["code"] == "unavailable-or-incompatible"


def test_page_limit_and_deadline_do_not_claim_a_complete_snapshot(saved, monkeypatch):
    calls = []
    def read(*args, **kwargs):
        calls.append(kwargs)
        return page(has_more=True, next_after=f"{len(calls):032x}.json")
    result = summary.read_summary(saved, ATTEMPT, read_page=read)
    assert len(calls) == 10
    assert result["engine"]["has_more"]
    assert result["summary"]["errors"] == [{"source": "engine", "code": "page-limit"}]
    calls.clear()
    times = iter([0, 1, 121])
    monkeypatch.setattr(summary.time, "monotonic", lambda: next(times))
    result = summary.read_summary(saved, ATTEMPT, read_page=read)
    assert len(calls) == 1 and calls[0]["timeout"] == 119
    assert result["summary"]["errors"] == [{"source": "engine", "code": "deadline"}]


@pytest.mark.parametrize("change", [
    {"attempt_id": "f" * 32}, {"operation_id": "f" * 32}, {"schema": "foreign"},
    {"version": True}, {"events": None},
])
def test_mismatched_execution_cannot_create_a_positive_fact(saved, change):
    row = execution()
    row["evidence"].update(change)
    result = summary.read_summary(saved, ATTEMPT, read_page=lambda *a, **kw: page(receipts=[row]))
    assert result["summary"]["boundaries"]["backend_start"]["status"] == "unknown"
    assert result["summary"]["errors"] == [{"source": "engine", "code": "invalid-receipt"}]


def test_unknown_native_and_incomplete_admission_keep_absence_unknown(saved):
    row = native()
    row["evidence"] = {"status": "unknown", "reason": "backend-ancestry-unconfirmed"}
    result = summary.read_summary(saved, ATTEMPT, read_page=lambda *a, **kw: page(
        receipts=[row], admissions=[admission(creation_complete=False)],
    ))
    assert all(value["status"] == "unknown" for value in result["summary"]["boundaries"].values())
    assert result["summary"]["errors"] == []


def test_changed_admissions_keep_observed_fact_and_report_non_atomic_change(saved):
    pages = iter([
        page(admissions=[admission()], has_more=True, next_after="c" * 32 + ".json"),
        page(admissions=[admission(creation_complete=False)]),
    ])
    result = summary.read_summary(saved, ATTEMPT, read_page=lambda *a, **kw: next(pages))
    assert result["summary"]["boundaries"]["allocation"]["status"] == "observed"
    assert result["summary"]["errors"] == [{"source": "admission", "code": "changed-between-pages"}]


def test_invalid_rows_and_source_errors_do_not_leak_or_hide_independent_evidence(saved):
    bad = copy.deepcopy(execution())
    bad["evidence"]["events"][-1]["details"]["pid"] = "private raw response"
    result = summary.read_summary(saved, ATTEMPT, read_page=lambda *a, **kw: page(
        receipts=[bad, native("e" * 32)],
        admissions=[admission(creation_complete="private raw response")],
        errors=[None, {"source": "private raw response", "code": "private raw response",
                       "operation_id": "private raw response"}],
    ))
    assert result["summary"]["boundaries"]["native_session"]["status"] == "observed"
    assert len(result["summary"]["errors"]) == 4
    assert "private raw response" not in json.dumps(result)


def test_receipt_page_overlap_and_duplicates_are_visible_not_double_counted(saved):
    pages = iter([
        page(receipts=[execution(), execution()], has_more=True,
             next_after="c" * 32 + ".execution.json"),
        page(receipts=[execution(), native("e" * 32)]),
    ])
    result = summary.read_summary(saved, ATTEMPT, read_page=lambda *a, **kw: next(pages))
    assert len(result["summary"]["boundaries"]["backend_start"]["evidence"]) == 1
    assert result["summary"]["boundaries"]["native_session"]["status"] == "observed"
    assert result["summary"]["errors"] == [
        {"source": "engine", "code": "invalid-receipt"},
        {"source": "engine", "code": "invalid-receipt"},
    ]


def test_summary_input_is_explicit_and_never_refreshed(saved, monkeypatch, capsys):
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **kw: page())
    assert history.main([ATTEMPT, "--summary", "--include-input", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["attempt"]["input"]["prompt"] == " synthetic private input\n "
    assert report["prompt_acceptance"] == "unknown"


def test_manager_event_and_reference_caps_remain_visible(saved, monkeypatch):
    detail = saved.detail(ATTEMPT)
    detail["events_has_more"] = True
    detail["events"] = [
        {"details": {"request_id": f"{index:032x}"}} for index in range(17)
    ] + [{"details": {"request_id": "unqualified-reference"}}]
    monkeypatch.setattr(saved, "detail", lambda *a, **kw: detail)
    calls = []
    def read(*args, **kwargs):
        calls.append(kwargs)
        return page()
    result = summary.read_summary(saved, ATTEMPT, read_page=read)
    assert len(calls[0]["request_ids"]) == 16
    assert result["summary"]["manager_events_has_more"]
    assert result["summary"]["errors"] == [
        {"source": "manager", "code": "invalid-reference"},
        {"source": "manager", "code": "reference-limit"},
        {"source": "manager", "code": "event-limit"},
    ]


@pytest.mark.parametrize("recorded,child,uncertain", [
    (0, 0, False), (3, 0, False), (7, 7, False), (3, None, True), (-9, -9, False),
    (0, 0, True),
])
def test_execution_terminal_keeps_recorded_child_and_unknown_wrapper_exits_distinct(
    saved, recorded, child, uncertain,
):
    row = execution()
    row["evidence"]["events"].append({
        "at": AT, "phase": "execution-returned", "details": {
            "exit_code": recorded, "process_exit_code": child, "receipt_uncertain": uncertain,
        },
    })
    result = summary.read_summary(saved, ATTEMPT, read_page=lambda *a, **kw: page(receipts=[row]))
    assert result["summary"]["owner_observations"]["execution"] == [{
        "operation_id": "c" * 32, "last_phase": "execution-returned", "at": AT,
        "terminal_phase": "execution-returned", "recorded_exit_code": recorded,
        "wrapper_exit_code": None,
        "resolution_id": "d" * 32, "parent_id": None, "worktree_id": "example-worktree",
        "creation_nonce": "nonce", "process_exit_code": child, "receipt_uncertain": uncertain,
    }]
    assert result["summary"]["boundaries"]["backend_start"]["status"] == "observed"
    assert result["summary"]["absence"] == result["prompt_acceptance"] == "unknown"
    assert result["summary"]["errors"] == []


@pytest.mark.parametrize("unknown", [False, True])
def test_execution_missing_and_explicit_unknown_terminals_never_imply_crash(saved, unknown):
    row = execution()
    if unknown:
        row["evidence"]["events"].append({"at": AT, "phase": "unknown", "details": {}})
    result = summary.read_summary(saved, ATTEMPT, read_page=lambda *a, **kw: page(receipts=[row]))
    observed = result["summary"]["owner_observations"]["execution"][0]
    assert observed["last_phase"] == ("unknown" if unknown else "process-spawned")
    assert observed["terminal_phase"] == ("unknown" if unknown else None)
    assert observed["wrapper_exit_code"] is observed["process_exit_code"] is None
    assert observed["receipt_uncertain"] is None
    assert result["summary"]["absence"] == result["prompt_acceptance"] == "unknown"
    assert "crash" not in json.dumps(result)


@pytest.mark.parametrize("phase,exit_code", [
    ("resolution-requested", None), ("plan-ready", 0), ("forwarded", 0),
    ("remote-admission-returned", 3), ("unknown", 2),
])
def test_resolution_observations_do_not_become_allocation_or_acceptance(saved, phase, exit_code):
    events = [{"at": AT, "phase": "resolution-requested", "details": {}}]
    if phase != "resolution-requested":
        events.append({"at": AT, "phase": phase, "details": {"exit_code": exit_code}})
    row = {"source": "resolution", "operation_id": "d" * 32, "evidence": {
        "schema": "worktree-launch-resolution", "version": 1,
        "attempt_id": ATTEMPT, "operation_id": "d" * 32, "events": events,
    }}
    result = summary.read_summary(saved, ATTEMPT, read_page=lambda *a, **kw: page(receipts=[row]))
    assert result["summary"]["owner_observations"]["resolution"] == [{
        "operation_id": "d" * 32, "last_phase": phase, "at": AT,
        "terminal_phase": phase if len(events) == 2 else None,
        "recorded_exit_code": exit_code, "wrapper_exit_code": None,
    }]
    assert all(value["status"] == "unknown" for value in result["summary"]["boundaries"].values())
    assert result["summary"]["errors"] == []


def test_each_execution_owner_remains_independent_across_pages_and_failure(saved):
    parent = execution(boundary="launcher-command")
    parent["evidence"]["events"].append({"at": AT, "phase": "execution-returned", "details": {
        "exit_code": 0, "process_exit_code": 0, "receipt_uncertain": False,
    }})
    child = execution("e" * 32)
    child["evidence"]["parent_id"] = "c" * 32
    child["evidence"]["events"].append({"at": AT, "phase": "unknown", "details": {}})
    calls = []
    def read(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 3:
            raise engine_client.EngineError("private process output")
        return page(receipts=[parent if len(calls) == 1 else child], has_more=True,
                    next_after=("c" if len(calls) == 1 else "e") * 32 + ".execution.json")
    result = summary.read_summary(saved, ATTEMPT, read_page=read)
    observations = result["summary"]["owner_observations"]["execution"]
    assert [row["operation_id"] for row in observations] == ["c" * 32, "e" * 32]
    assert observations[0]["terminal_phase"] == "execution-returned"
    assert observations[1]["parent_id"] == "c" * 32
    assert observations[1]["terminal_phase"] == "unknown"
    assert result["summary"]["boundaries"]["launcher_start"]["status"] == "observed"
    assert result["summary"]["boundaries"]["backend_start"]["status"] == "observed"
    assert result["summary"]["errors"] == [{"source": "engine", "code": "unavailable-or-incompatible"}]


def test_admission_progress_is_deduplicated_without_inventing_a_terminal(saved):
    pages = iter([
        page(admissions=[admission(creation_complete=False, admission_accepted=False)],
             has_more=True, next_after="c" * 32 + ".json"),
        page(admissions=[admission()], has_more=True, next_after="e" * 32 + ".json"),
        page(admissions=[admission()]),
    ])
    result = summary.read_summary(saved, ATTEMPT, read_page=lambda *a, **kw: next(pages))
    observed = result["summary"]["owner_observations"]["admission"]
    assert len(observed) == 2
    assert not observed[0]["creation_complete"] and not observed[0]["admission_accepted"]
    assert observed[1]["creation_complete"] and observed[1]["admission_accepted"]
    assert all("terminal_phase" not in row for row in observed)
    assert result["summary"]["errors"] == [{"source": "admission", "code": "changed-between-pages"}]


@pytest.mark.parametrize("change", [
    {"exit_code": True}, {"exit_code": "private exit"},
    {"process_exit_code": False}, {"receipt_uncertain": "private uncertainty"},
])
def test_invalid_terminal_retains_independent_positive_process_evidence(saved, change):
    row = execution()
    row["evidence"]["events"].append({"at": AT, "phase": "execution-returned", "details": {
        "exit_code": 3, "process_exit_code": 0, "receipt_uncertain": False, **change,
    }})
    result = summary.read_summary(saved, ATTEMPT, read_page=lambda *a, **kw: page(receipts=[row]))
    assert result["summary"]["boundaries"]["backend_start"]["status"] == "observed"
    assert result["summary"]["owner_observations"]["execution"] == []
    assert result["summary"]["errors"] == [{"source": "engine", "code": "invalid-receipt"}]
    assert "private" not in json.dumps(result)
