"""Read-only bounded joins preserve partial evidence without inventing acceptance."""

from __future__ import annotations

import argparse
import json
from uuid import uuid4

import pytest

from agent_worktrees import launch_attempt, launch_attempt_inspection as inspection
from agent_worktrees import launch_execution, native_source_context, output


def resolution(directory, attempt_id, operation_id, phase="plan-ready"):
    path = launch_attempt._path(directory, attempt_id, operation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "schema": "worktree-launch-resolution", "version": 1,
        "attempt_id": attempt_id, "operation_id": operation_id, "events": [
            {"at": "2026-10-24T00:00:00+00:00", "phase": "resolution-requested", "details": {}},
            {"at": "2026-10-24T00:00:01+00:00", "phase": phase,
             "details": {"worktree_id": "example-worktree", "exit_code": 0}},
        ],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def admission(directory, attempt, request):
    path = inspection.launch_request._admission_path(directory, request)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({
        "version": 2, "request_id": request, "native_seed_required": True,
        "suffix": request, "timestamp": "20261024-000000", "prior_revision": 0,
        "attempt_id": attempt, "worktree_id": "example-worktree", "seed_id": request,
        "kind": "new", "creation_complete": True, "accepted": True,
        "text": "private legacy data", "fingerprint": "f" * 64,
    }), encoding="utf-8")


def test_missing_evidence_is_unknown_without_creation(tmp_path):
    result = inspection.inspect(tmp_path, uuid4().hex)
    assert result["errors"] == [{"source": "receipts", "code": "missing"}]
    assert result["prompt_acceptance"] == "unknown"
    assert not result["receipts"] and list(tmp_path.iterdir()) == []


def test_resolution_pages_are_ordered_bounded_read_only_and_never_success(tmp_path):
    attempt = uuid4().hex
    paths = [resolution(tmp_path, attempt, str(i) * 32) for i in (3, 1, 2)]
    before = {path: path.read_bytes() for path in paths}
    first = inspection.inspect(tmp_path, attempt, limit=2)
    assert [item["operation_id"] for item in first["receipts"]] == ["1" * 32, "2" * 32]
    assert first["has_more"] and first["next_after"] == "2" * 32 + ".json"
    second = inspection.inspect(tmp_path, attempt, limit=2, after=first["next_after"])
    assert [item["operation_id"] for item in second["receipts"]] == ["3" * 32]
    assert not second["has_more"] and second["next_after"] is None
    assert all(report["prompt_acceptance"] == "unknown" for report in (first, second))
    assert {path: path.read_bytes() for path in paths} == before


def test_corruption_is_per_source_and_never_echoes_private_bytes(tmp_path):
    attempt = uuid4().hex
    bad = resolution(tmp_path, attempt, "1" * 32)
    bad.write_text('{"private":"must not be printed"}', encoding="utf-8")
    resolution(tmp_path, attempt, "2" * 32)
    result = inspection.inspect(tmp_path, attempt)
    assert result["errors"] == [
        {"source": "resolution", "operation_id": "1" * 32, "code": "unreadable"},
    ]
    assert len(result["receipts"]) == 1
    assert "must not be printed" not in json.dumps(result)


def test_work_budget_refuses_arbitrary_partial_directory(tmp_path, monkeypatch):
    attempt = uuid4().hex
    for i in range(3):
        resolution(tmp_path, attempt, str(i) * 32)
    monkeypatch.setattr(inspection, "MAX_ENTRIES", 2)
    result = inspection.inspect(tmp_path, attempt, limit=1)
    assert not result["receipts"] and result["errors"][0]["code"] == "unreadable"


@pytest.mark.parametrize("arguments", [
    {"limit": 0}, {"limit": 101}, {"limit": True}, {"after": "../unsafe"},
    {"request_ids": ("invalid",)}, {"request_ids": tuple(["a" * 32] * 17)},
])
def test_invalid_queries_refuse_before_filesystem_access(tmp_path, arguments):
    with pytest.raises(ValueError):
        inspection.inspect(tmp_path, "a" * 32, **arguments)
    assert list(tmp_path.iterdir()) == []


def test_hard_linked_receipt_is_not_followed(tmp_path):
    attempt = uuid4().hex
    path = resolution(tmp_path, attempt, "1" * 32)
    import os
    os.link(path, tmp_path / "second-link")
    result = inspection.inspect(tmp_path, attempt)
    assert result["errors"][0]["code"] == "unreadable"
    assert not result["receipts"]


def test_execution_uses_actual_owner_reader_and_native_join(tmp_path):
    attempt, operation, resolution_id = (uuid4().hex for _ in range(3))
    path = launch_attempt._path(tmp_path, attempt, operation, execution=True)
    path.parent.mkdir(parents=True)
    value = {
        "schema": "worktree-launch-execution", "version": 2,
        "attempt_id": attempt, "resolution_id": resolution_id, "operation_id": operation,
        "parent_id": None, "worktree_id": "example-worktree", "creation_nonce": "nonce",
        "native_epoch": {
            "platform": "linux", "environment_id": "e" * 32,
            "boot_id": "00000000-0000-4000-8000-000000000001",
        }, "events": [
            {"at": "2026-10-24T00:00:00+00:00", "phase": "execution-requested", "details": {}},
            {"at": "2026-10-24T00:00:01+00:00", "phase": "process-spawned", "details": {
                "pid": 42, "process_start_time": "100", "boundary": "backend-command",
            }},
        ],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    assert launch_execution.read(tmp_path, attempt, operation) == value
    native_path = launch_attempt._path(tmp_path, attempt, operation, native=True)
    native_session = "00000000-0000-4000-8000-000000000002"
    native_path.write_text(json.dumps({
        "schema": "worktree-launch-native-observation", "version": 1,
        "attempt_id": attempt, "resolution_id": resolution_id, "operation_id": operation,
        "worktree_id": "example-worktree", "creation_nonce": "nonce",
        "native_session_id": native_session, "native_pid": 42, "native_birth": "100",
        "native_epoch": value["native_epoch"],
        "source_identity": native_source_context.identity(native_session, 42, "100", value["native_epoch"]),
        "ancestors": [{"pid": 42, "start_time": "100"}], "at": "2026-10-24T00:00:02+00:00",
    }), encoding="utf-8")
    result = inspection.inspect(tmp_path, attempt)
    assert {row["source"] for row in result["receipts"]} == {"execution", "native"}
    assert result["prompt_acceptance"] == "unknown"
    native = next(row["evidence"] for row in result["receipts"] if row["source"] == "native")
    assert native["status"] == "native-session-observed" and native["native_session_id"] == native_session


def test_corrupt_orphan_native_is_not_hidden_by_missing_execution(tmp_path):
    attempt, operation = uuid4().hex, uuid4().hex
    path = launch_attempt._path(tmp_path, attempt, operation, native=True)
    path.parent.mkdir(parents=True)
    path.write_text("private invalid witness", encoding="utf-8")
    result = inspection.inspect(tmp_path, attempt)
    assert result["errors"] == [{"source": "native", "operation_id": operation, "code": "unreadable"}]


def test_admission_is_projected_and_bound_without_seed_or_current_checkout_reads(tmp_path, monkeypatch):
    attempt, request = uuid4().hex, uuid4().hex
    admission(tmp_path, attempt, request)
    monkeypatch.setattr(inspection.launch_request, "inspect", lambda *args: pytest.fail("live seed read"))
    result = inspection.inspect(tmp_path, attempt, request_ids=(request,))
    assert result["admissions"][0]["admission_accepted"] is True
    assert result["admissions"][0]["prompt_acceptance"] == "unknown"
    assert "private legacy data" not in json.dumps(result)
    resolved = resolution(tmp_path, attempt, "1" * 32)
    saved = json.loads(resolved.read_text(encoding="utf-8"))
    saved["events"][-1]["details"]["request_id"] = request
    resolved.write_text(json.dumps(saved), encoding="utf-8")
    discovered = inspection.inspect(tmp_path, attempt)
    assert discovered["admissions"] == result["admissions"]
    assert discovered["errors"] == []
    mismatch = inspection.inspect(tmp_path, uuid4().hex, request_ids=(request,))
    assert not mismatch["admissions"]
    assert mismatch["errors"][-1]["source"] == "admission"


def test_unreadable_receipt_directory_does_not_withhold_safe_admission(tmp_path, monkeypatch):
    attempt, request = uuid4().hex, uuid4().hex
    admission(tmp_path, attempt, request)
    def refused_path(*args, **kwargs):
        raise ValueError("Indirect receipt directory")
    monkeypatch.setattr(inspection.launch_attempt, "_path", refused_path)
    result = inspection.inspect(tmp_path, attempt, request_ids=(request,))
    assert result["errors"] == [{"source": "receipts", "code": "unreadable"}]
    assert result["admissions"][0]["request_id"] == request
    assert result["prompt_acceptance"] == "unknown"


def test_cli_parser_and_handler_are_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(inspection.cfg, "tracking_dir", lambda: tmp_path)
    parser = argparse.ArgumentParser()
    inspection.add_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["launch-attempts", uuid4().hex, "--json"])
    with output.capture_json_output() as captured:
        assert inspection.cmd_launch_attempts(args) == 0
    assert json.loads(captured.getvalue())["schema"] == inspection.SCHEMA
    assert list(tmp_path.iterdir()) == []
